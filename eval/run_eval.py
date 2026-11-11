"""Evaluation: catch rate, false alarm rate and speed, against a naive LLM baseline.

1. Generate 40 analyses with the LLM from real filing figures (5 companies).
2. Verify each untouched analysis: any red flag here is a candidate false alarm.
3. Inject one known error into copies (value, year, scale, growth) and keep labels.
4. Run every copy through ProofLine and through the naive checker.
5. Write eval/results.json, eval/dataset.json and eval/labels.json.

Usage:  uv run python eval/run_eval.py [--workers N]
"""
from __future__ import annotations

import json
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from eval.common import (  # noqa: E402
    AMBER_STATUSES, COMPANY_NAMES, DEFAULT_WORKERS, RED_STATUSES, ROOT, now, overlapping, rate,
    run_parallel, store,
)
from backend import guard, llm, naive  # noqa: E402
from backend.edgar import METRICS  # noqa: E402
from backend.pipeline import verify_report  # noqa: E402

RESULTS_PATH = ROOT / "eval" / "results.json"
DATASET_PATH = ROOT / "eval" / "dataset.json"
LABELS_PATH = ROOT / "eval" / "labels.json"
SEED = 20261111
YEARS = (2022, 2023, 2024, 2025)
FOCUSES = (
    ("profitability", ("revenue", "gross_profit", "operating_income", "net_income", "eps_diluted", "rd_expense")),
    ("balance sheet and cash", ("revenue", "net_income", "total_assets", "total_liabilities", "cash", "operating_cash_flow")),
)
ERROR_TYPES = (
    {"id": "value", "label": "Value changed by 5 to 30%", "expected": "WRONG_VALUE"},
    {"id": "year", "label": "Figure from the wrong year", "expected": "WRONG_PERIOD"},
    {"id": "scale", "label": "Millions swapped with billions", "expected": "SCALE_ERROR"},
    {"id": "growth", "label": "Growth rate changed", "expected": "WRONG_VALUE"},
)
WRITER_SYSTEM = (
    "You are a sell-side equity analyst. Write a short analysis note in plain prose (no bullet points, no "
    "markdown, no headings) of about 110 to 140 words. Use only the figures in the fact sheet. State dollar "
    "amounts in billions with one decimal place (for example $391.0 billion), percentages with one decimal "
    "place, and earnings per share to the cent. Say which fiscal year each figure refers to. Do not add any "
    "figure that is not in the fact sheet, and do not round figures further."
)


def fact_sheet(fact_store, ticker: str, year: int, metrics: tuple[str, ...]) -> str:
    lines = [f"{COMPANY_NAMES[ticker]} ({ticker}), fiscal {year} and fiscal {year - 1}"]
    values: dict[tuple[str, int], float] = {}
    for metric in metrics:
        for y in (year, year - 1):
            fact = fact_store.get_fact(ticker, metric, y)
            if fact is None:
                continue
            values[(metric, y)] = fact.value
            shown = f"${fact.value:,.2f} per share" if METRICS[metric].unit == "USD/shares" else f"${fact.value / 1e9:,.1f} billion"
            lines.append(f"- {METRICS[metric].label}, fiscal {y}: {shown}")
    if ("revenue", year) in values and ("revenue", year - 1) in values:
        growth = (values[("revenue", year)] - values[("revenue", year - 1)]) / abs(values[("revenue", year - 1)]) * 100
        lines.append(f"- Revenue growth, fiscal {year} over fiscal {year - 1}: {growth:+.1f}%")
    if ("net_income", year) in values and ("net_income", year - 1) in values and values[("net_income", year - 1)] > 0:
        growth = (values[("net_income", year)] - values[("net_income", year - 1)]) / values[("net_income", year - 1)] * 100
        lines.append(f"- Net income growth, fiscal {year} over fiscal {year - 1}: {growth:+.1f}%")
    for metric, label in (("gross_profit", "Gross margin"), ("operating_income", "Operating margin")):
        if (metric, year) in values and ("revenue", year) in values:
            lines.append(f"- {label}, fiscal {year}: {values[(metric, year)] / values[('revenue', year)] * 100:.1f}%")
    return "\n".join(lines)


def generate(spec: dict) -> dict:
    result = llm.complete(system=WRITER_SYSTEM, user=f"Fact sheet:\n{spec['sheet']}\n\nWrite the note.", effort="low")
    return {**spec, "text": str(result.data).strip(), "writer_model": result.model}


def verify(text: str) -> dict:
    return verify_report(text, verify.store, extractor="llm")  # type: ignore[attr-defined]


def _mutations(doc: dict, result: dict, fact_store, rng: random.Random) -> list[dict]:
    """One injected error of each type, where the analysis has a suitable figure."""
    text = result["report"]["text"]
    verified = [v for v in result["verdicts"] if v["status"] == "VERIFIED" and v.get("token")]
    amounts = [v for v in verified if v["claim"]["kind"] == "absolute" and v["claim"]["unit"] == "USD"
               and "illion" in v["token"]["text"]]
    growths = [v for v in verified if v["claim"]["kind"] == "growth_pct"]
    out: list[dict] = []
    used: set[int] = set()

    def add(error: str, verdict: dict, replacement: str) -> None:
        token = verdict["token"]
        if replacement == token["text"] or token["start"] in used:
            return
        used.add(token["start"])
        mutated = text[:token["start"]] + replacement + text[token["end"]:]
        out.append({
            "doc_id": f"{doc['id']}-{error}", "base_id": doc["id"], "error": error, "text": mutated,
            "start": token["start"], "end": token["start"] + len(replacement),
            "original": token["text"], "replacement": replacement,
            "metric": verdict["claim"]["metric"], "fiscal_year": verdict["claim"]["fiscal_year"],
        })

    rng.shuffle(amounts)
    for verdict in amounts:
        factor = rng.choice((rng.uniform(0.70, 0.95), rng.uniform(1.05, 1.30)))
        add("value", verdict, guard.format_like(verdict["token"]["text"], verdict["claimed"] * factor, "USD"))
        if any(m["error"] == "value" for m in out):
            break
    for verdict in amounts:
        claim = verdict["claim"]
        ticker = fact_store.resolve(claim["company"])
        other = fact_store.get_fact(ticker, claim["metric"], claim["fiscal_year"] - 1) if ticker else None
        if other is None:
            continue
        add("year", verdict, guard.format_like(verdict["token"]["text"], abs(other.value), "USD"))
        if any(m["error"] == "year" for m in out):
            break
    for verdict in amounts:
        add("scale", verdict, verdict["token"]["text"].replace("billion", "million"))
        if any(m["error"] == "scale" for m in out):
            break
    for verdict in growths:
        delta = rng.choice((-1, 1)) * rng.uniform(3.0, 15.0)
        original = verdict["token"]["text"]
        number = abs(verdict["claimed"]) + abs(delta) if original[0] not in "-+" else verdict["claimed"] + delta
        add("growth", verdict, f"{number:.1f}%")
        if any(m["error"] == "growth" for m in out):
            break
    return out


def _score(result: dict, start: int | None, end: int | None) -> dict:
    """Outcome at the injected figure, and flags on every other figure."""
    target_v, target_u = overlapping(result, start, end) if start is not None else ([], [])
    target_ids = {id(x) for x in target_v + target_u}
    outcome = None
    if start is not None:
        statuses = [v["status"] for v in target_v]
        if any(s in RED_STATUSES for s in statuses):
            outcome = next(s for s in statuses if s in RED_STATUSES)
        elif any(s in AMBER_STATUSES for s in statuses):
            outcome = "UNSUPPORTED"
        elif target_u:
            outcome = "UNCHECKED"
        else:
            outcome = "MISSED"
    others = [v for v in result["verdicts"] if id(v) not in target_ids]
    other_unchecked = [u for u in result["unchecked"] if id(u) not in target_ids]
    false_reds = [
        {"text": v["token"]["text"] if v.get("token") else v["claim"]["span"], "status": v["status"],
         "reason": v["reason"], "claim": v["claim"]}
        for v in others if v["status"] in RED_STATUSES
    ]
    return {
        "outcome": outcome,
        "untouched": len(others) + len(other_unchecked),
        "untouched_red": len(false_reds),
        "untouched_amber": sum(1 for v in others if v["status"] in AMBER_STATUSES) + len(other_unchecked),
        "false_reds": false_reds,
        "extract_ms": result["timings"]["extract_ms"],
        "check_ms": result["timings"]["check_ms"] + result["timings"]["prepare_ms"],
        "cached": result["extractor"]["cached"],
    }


def main(argv: list[str]) -> int:
    workers = int(argv[argv.index("--workers") + 1]) if "--workers" in argv else DEFAULT_WORKERS
    fact_store = store()
    verify.store = fact_store  # type: ignore[attr-defined]
    rng = random.Random(SEED)
    specs = []
    for ticker in COMPANY_NAMES:
        for year in YEARS:
            for focus, metrics in FOCUSES:
                specs.append({"id": f"{ticker}-FY{year}-{focus.split()[0]}", "ticker": ticker, "year": year,
                              "focus": focus, "sheet": fact_sheet(fact_store, ticker, year, metrics)})
    print(f"generating {len(specs)} analyses with {llm.default_model()} via {llm.provider()}")
    docs = [d for d in run_parallel(generate, specs, workers=workers, label="generate") if not isinstance(d, Exception)]

    print("verifying untouched analyses")
    clean_results = run_parallel(verify, [d["text"] for d in docs], workers=workers, label="verify clean")
    pairs = [(d, r) for d, r in zip(docs, clean_results) if not isinstance(r, Exception)]

    injected: list[dict] = []
    for doc, result in pairs:
        injected += _mutations(doc, result, fact_store, rng)
    print(f"{len(injected)} injected copies")
    injected_results = run_parallel(verify, [m["text"] for m in injected], workers=workers, label="verify injected")
    print("naive baseline")
    naive_clean = run_parallel(naive.naive_check, [d["text"] for d, _ in pairs], workers=workers, label="naive clean")
    naive_injected = run_parallel(naive.naive_check, [m["text"] for m in injected], workers=workers, label="naive injected")

    clean_scores = [_score(r, None, None) for _, r in pairs]
    rows = []
    for mutation, result, review in zip(injected, injected_results, naive_injected):
        if isinstance(result, Exception):
            continue
        rows.append({**mutation, **_score(result, mutation["start"], mutation["end"]),
                     "naive_flagged": None if isinstance(review, Exception) else not review["accurate"]})

    by_type = []
    for error in ERROR_TYPES:
        subset = [r for r in rows if r["error"] == error["id"]]
        caught = sum(1 for r in subset if r["outcome"] in RED_STATUSES)
        amber = sum(1 for r in subset if r["outcome"] in ("UNSUPPORTED", "UNCHECKED"))
        exact = sum(1 for r in subset if r["outcome"] == error["expected"])
        judged = [r for r in subset if r["naive_flagged"] is not None]
        naive_caught = sum(1 for r in judged if r["naive_flagged"])
        by_type.append({
            **error, "n": len(subset),
            "caught": caught, "catch_rate": rate(caught, len(subset)),
            "flagged_amber": amber, "missed": len(subset) - caught - amber,
            "exact_diagnosis": exact, "exact_rate": rate(exact, len(subset)),
            "naive_caught": naive_caught, "naive_catch_rate": rate(naive_caught, len(judged)),
        })
    total = len(rows)
    caught_all = sum(t["caught"] for t in by_type)
    untouched = sum(s["untouched"] for s in clean_scores) + sum(r["untouched"] for r in rows)
    untouched_red = sum(s["untouched_red"] for s in clean_scores) + sum(r["untouched_red"] for r in rows)
    untouched_amber = sum(s["untouched_amber"] for s in clean_scores) + sum(r["untouched_amber"] for r in rows)
    clean_numbers = sum(s["untouched"] for s in clean_scores)
    clean_red = sum(s["untouched_red"] for s in clean_scores)
    fresh = [s for s in clean_scores + rows if not s["cached"]]
    naive_clean_ok = [r for r in naive_clean if not isinstance(r, Exception)]
    naive_judged = [r for r in rows if r["naive_flagged"] is not None]
    payload = {
        "generated_at": now(),
        "model": llm.default_model(),
        "provider": llm.provider(),
        "analyses": len(pairs),
        "injected": total,
        "by_error_type": by_type,
        "overall": {
            "catch_rate": rate(caught_all, total),
            "flagged_rate": rate(caught_all + sum(t["flagged_amber"] for t in by_type), total),
            "missed": sum(t["missed"] for t in by_type),
            "naive_catch_rate": rate(sum(1 for r in naive_judged if r["naive_flagged"]), len(naive_judged)),
        },
        "false_alarms": {
            "untouched_numbers": untouched,
            "red_flags": untouched_red,
            "rate": rate(untouched_red, untouched),
            "could_not_check": untouched_amber,
            "could_not_check_rate": rate(untouched_amber, untouched),
            "clean_numbers": clean_numbers,
            "clean_red_flags": clean_red,
            "clean_reports_certified": sum(1 for _, r in pairs if r["summary"]["status"] == "CERTIFIED"),
            "naive_flagged_clean": sum(1 for r in naive_clean_ok if not r["accurate"]),
            "naive_clean_total": len(naive_clean_ok),
            "naive_false_alarm_rate": rate(sum(1 for r in naive_clean_ok if not r["accurate"]), len(naive_clean_ok)),
            "examples": [fr for s in clean_scores for fr in s["false_reds"]][:20],
        },
        "timing": {
            "reports_timed": len(fresh),
            "avg_extract_ms": round(sum(s["extract_ms"] for s in fresh) / len(fresh), 1) if fresh else None,
            "avg_check_ms": round(sum(s["check_ms"] for s in clean_scores + rows) / max(1, len(clean_scores) + len(rows)), 3),
            "avg_numbers_per_report": round(clean_numbers / max(1, len(pairs)), 1),
        },
        "misses": [
            {k: r[k] for k in ("doc_id", "error", "original", "replacement", "outcome", "metric")}
            for r in rows if r["outcome"] not in RED_STATUSES
        ],
    }
    RESULTS_PATH.write_text(json.dumps(payload, indent=1), encoding="utf-8")
    DATASET_PATH.write_text(json.dumps([{k: d[k] for k in ("id", "ticker", "year", "focus", "sheet", "text")}
                                        for d, _ in pairs], indent=1), encoding="utf-8")
    LABELS_PATH.write_text(json.dumps([{k: m[k] for k in ("doc_id", "base_id", "error", "start", "end", "original",
                                                          "replacement", "metric", "fiscal_year", "text")}
                                       for m in injected], indent=1), encoding="utf-8")
    print(f"\nwrote {RESULTS_PATH}")
    for t in by_type:
        print(f"  {t['id']:8s} n={t['n']:3d} caught={t['catch_rate'] * 100:5.1f}% exact={t['exact_rate'] * 100:5.1f}% "
              f"amber={t['flagged_amber']} missed={t['missed']} naive={t['naive_catch_rate'] * 100:5.1f}%")
    fa = payload["false_alarms"]
    print(f"  false alarms: {fa['red_flags']}/{fa['untouched_numbers']} ({fa['rate'] * 100:.2f}%), "
          f"could not check {fa['could_not_check']}; naive flagged {fa['naive_flagged_clean']}/{fa['naive_clean_total']} clean")
    print(f"  timing: {payload['timing']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

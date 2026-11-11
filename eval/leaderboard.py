"""Model leaderboard: how often do LLMs get financial figures wrong?

Sixty questions across five companies go to several models in two modes: from
memory, and with the 10-K financial statements supplied. Every answer is scored
by ProofLine. No human labelling. The five worst real answers are saved to
samples/real_failures/ for the demo.

Usage:  uv run python eval/leaderboard.py [--workers N] [--models a,b,c]
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from eval.common import (  # noqa: E402
    AMBER_STATUSES, COMPANY_NAMES, DEFAULT_WORKERS, RED_STATUSES, ROOT, now, rate, run_parallel, store,
)
from backend import config, guard, llm  # noqa: E402
from backend.edgar import METRICS  # noqa: E402
from backend.pipeline import verify_report  # noqa: E402

RESULTS_PATH = ROOT / "eval" / "leaderboard.json"
QUESTIONS_PATH = ROOT / "eval" / "questions.json"
FAILURES_DIR = ROOT / "samples" / "real_failures"
MODELS = ("claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5")
MODEL_LABELS = {"claude-opus-5-5": "Claude Opus 5.5", "claude-sonnet-5-5": "Claude Sonnet 5.5",
                "claude-haiku-5-5": "Claude Haiku 5.5"}
MODES = ({"id": "memory", "label": "From memory"}, {"id": "source", "label": "10-K supplied"})
CATEGORIES = {
    "multi_year_growth": "Multi-year growth",
    "margins": "Margins",
    "cross_company": "Company comparisons",
    "fiscal_calendar": "Fiscal vs calendar year",
    "per_share": "Per-share figures",
}
WORST_EXAMPLES = 5


def _label(metric: str) -> str:
    return METRICS[metric].label.lower()


def build_questions() -> list[dict]:
    """Sixty questions, weighted to what models get wrong."""
    questions: list[dict] = []

    def add(category: str, text: str, filings: list[tuple[str, int]]) -> None:
        questions.append({"id": f"q{len(questions) + 1:02d}", "category": category, "question": text,
                          "filings": [{"ticker": t, "year": y} for t, y in filings]})

    for ticker, name in COMPANY_NAMES.items():
        for metric, y0, y1 in (("revenue", 2023, 2025), ("net_income", 2022, 2024), ("operating_income", 2023, 2025)):
            add("multi_year_growth",
                f"By what percentage did {name}'s {_label(metric)} change from fiscal {y0} to fiscal {y1}? "
                f"State the figure for each of the two years and the percentage change.", [(ticker, y1)])
    for ticker in ("AAPL", "MSFT", "NVDA", "TSLA"):
        for year in (2023, 2024, 2025):
            add("margins",
                f"What were {COMPANY_NAMES[ticker]}'s gross margin and operating margin in fiscal {year}? "
                f"Give revenue, gross profit and operating income for that year as well.", [(ticker, year)])
    for a, b, metric, year in (
        ("AAPL", "MSFT", "revenue", 2024), ("AMZN", "AAPL", "revenue", 2025), ("NVDA", "TSLA", "net_income", 2025),
        ("MSFT", "AMZN", "operating_income", 2024), ("AAPL", "NVDA", "net_income", 2025), ("TSLA", "NVDA", "revenue", 2024),
        ("MSFT", "AAPL", "rd_expense", 2025), ("AMZN", "MSFT", "net_income", 2023), ("NVDA", "MSFT", "gross_profit", 2025),
        ("AAPL", "AMZN", "cash", 2024), ("TSLA", "AAPL", "rd_expense", 2024), ("AMZN", "NVDA", "total_assets", 2025),
    ):
        add("cross_company",
            f"Compare {COMPANY_NAMES[a]} and {COMPANY_NAMES[b]} on {_label(metric)} for fiscal {year}. "
            f"Give both figures and say how many times larger the bigger one is.", [(a, year), (b, year)])
    for year in (2023, 2024, 2025):
        add("fiscal_calendar",
            f"Microsoft's fiscal year ends on June 30. What revenue and net income did it report for the fiscal year "
            f"ended June 30, {year}, and by what percentage did revenue change from the prior fiscal year?", [("MSFT", year)])
        add("fiscal_calendar",
            f"NVIDIA's fiscal {year} ended in late January {year}. What revenue and net income did it report for "
            f"fiscal {year}, and what was revenue growth over fiscal {year - 1}?", [("NVDA", year)])
        add("fiscal_calendar",
            f"Apple's fiscal year ends in late September. What were its revenue, net income and total assets for "
            f"fiscal {year}?", [("AAPL", year)])
    for ticker in ("AAPL", "MSFT", "TSLA", "AMZN"):
        for year in (2023, 2024, 2025):
            add("per_share",
                f"What was {COMPANY_NAMES[ticker]}'s diluted EPS in fiscal {year} and in fiscal {year - 1}, and what "
                f"was the percentage change? Also give net income for fiscal {year}.", [(ticker, year)])
    return questions


def filing_context(question: dict) -> str:
    parts = []
    for filing in question["filings"]:
        path = config.FILINGS_DIR / f"{filing['ticker']}_FY{filing['year']}.txt"
        if path.exists():
            parts.append(path.read_text(encoding="utf-8"))
    return "\n\n".join(parts)


def run_task(task: dict) -> dict:
    question = task["question"]
    context = filing_context(question) if task["mode"] == "source" else None
    answer = guard.agent_answer(question["question"], model=task["model"], context=context)
    result = verify_report(answer["answer"], run_task.store, extractor="llm")  # type: ignore[attr-defined]
    wrong = [
        {"text": v["token"]["text"] if v.get("token") else v["claim"]["span"], "status": v["status"],
         "reason": v["reason"], "metric": v["claim"]["metric"], "fiscal_year": v["claim"]["fiscal_year"]}
        for v in result["verdicts"] if v["status"] in RED_STATUSES
    ]
    counts = result["summary"]["counts"]
    return {
        "question_id": question["id"], "category": question["category"], "question": question["question"],
        "model": task["model"], "mode": task["mode"], "answer": answer["answer"],
        "numbers": result["summary"]["numbers"], "verified": counts["VERIFIED"],
        "amber": counts["UNSUPPORTED"] + counts["UNCHECKED"], "counts": counts, "wrong": wrong,
        "status": result["summary"]["status"],
    }


def summarise(rows: list[dict]) -> dict:
    n = len(rows)
    with_wrong = sum(1 for r in rows if r["wrong"])
    numbers = sum(r["numbers"] for r in rows)
    by_verdict = {status: sum(r["counts"][status] for r in rows) for status in ("WRONG_VALUE", "WRONG_PERIOD", "SCALE_ERROR")}
    wrong_numbers = sum(by_verdict.values())
    return {
        "answers": n,
        "answers_with_wrong_number": with_wrong,
        "wrong_answer_rate": rate(with_wrong, n),
        "numbers": numbers,
        "wrong_numbers": wrong_numbers,
        "wrong_number_rate": rate(wrong_numbers, numbers),
        "verified_numbers": sum(r["verified"] for r in rows),
        "unverifiable_numbers": sum(r["amber"] for r in rows),
        "fully_certified_answers": sum(1 for r in rows if r["status"] == "CERTIFIED"),
        "answers_without_figures": sum(1 for r in rows if r["numbers"] == 0),
        "by_verdict": by_verdict,
    }


def main(argv: list[str]) -> int:
    workers = int(argv[argv.index("--workers") + 1]) if "--workers" in argv else DEFAULT_WORKERS
    models = tuple(argv[argv.index("--models") + 1].split(",")) if "--models" in argv else MODELS
    run_task.store = store()  # type: ignore[attr-defined]
    questions = build_questions()
    QUESTIONS_PATH.write_text(json.dumps(questions, indent=1), encoding="utf-8")
    tasks = [{"question": q, "model": m, "mode": mode["id"]} for m in models for mode in MODES for q in questions]
    print(f"{len(questions)} questions x {len(models)} models x {len(MODES)} modes = {len(tasks)} answers; "
          f"scored with {llm.default_model()} via {llm.provider()}")
    outcomes = run_parallel(run_task, tasks, workers=workers, label="leaderboard")
    rows = [o for o in outcomes if not isinstance(o, Exception)]
    failed = len(outcomes) - len(rows)
    for task, outcome in zip(tasks, outcomes):
        if isinstance(outcome, Exception):
            print(f"  FAILED {task['model']} {task['mode']} {task['question']['id']}: {outcome}")

    table = []
    for model in models:
        for mode in MODES:
            subset = [r for r in rows if r["model"] == model and r["mode"] == mode["id"]]
            table.append({
                "model": model, "model_label": MODEL_LABELS.get(model, model), "mode": mode["id"],
                "mode_label": mode["label"], **summarise(subset),
                "by_category": {
                    key: {"answers": len(cat), "with_wrong": sum(1 for r in cat if r["wrong"]),
                          "rate": rate(sum(1 for r in cat if r["wrong"]), len(cat))}
                    for key in CATEGORIES for cat in [[r for r in subset if r["category"] == key]]
                },
            })

    failures = sorted((r for r in rows if r["wrong"]), key=lambda r: (-len(r["wrong"]), r["mode"] != "memory", r["question_id"]))
    worst: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for row in failures:  # spread the examples across models and modes first
        key = (row["model"], row["mode"])
        if key not in seen:
            worst.append(row)
            seen.add(key)
        if len(worst) == WORST_EXAMPLES:
            break
    for row in failures:
        if len(worst) == WORST_EXAMPLES:
            break
        if row not in worst:
            worst.append(row)
    FAILURES_DIR.mkdir(parents=True, exist_ok=True)
    for old in FAILURES_DIR.glob("*.txt"):
        old.unlink()
    saved = []
    for index, row in enumerate(worst, start=1):
        name = f"{index}_{row['model'].replace('claude-', '')}_{row['mode']}_{row['question_id']}.txt"
        (FAILURES_DIR / name).write_text(row["answer"] + "\n", encoding="utf-8")
        saved.append({
            "id": f"failure-{index}", "file": f"real_failures/{name}", "model": row["model"],
            "model_label": MODEL_LABELS.get(row["model"], row["model"]), "mode": row["mode"],
            "question": row["question"], "answer": row["answer"], "wrong": row["wrong"], "category": row["category"],
        })
    (FAILURES_DIR / "index.json").write_text(json.dumps(saved, indent=1), encoding="utf-8")

    payload = {
        "generated_at": now(),
        "scorer_model": llm.default_model(),
        "provider": llm.provider(),
        "questions": len(questions),
        "models": [{"id": m, "label": MODEL_LABELS.get(m, m)} for m in models],
        "modes": list(MODES),
        "categories": CATEGORIES,
        "failed_runs": failed,
        "overall": {mode["id"]: summarise([r for r in rows if r["mode"] == mode["id"]]) for mode in MODES},
        "table": table,
        "failures": saved,
        "answers": [{k: r[k] for k in ("question_id", "category", "model", "mode", "numbers", "verified", "amber",
                                       "status", "wrong")} for r in rows],
    }
    RESULTS_PATH.write_text(json.dumps(payload, indent=1), encoding="utf-8")
    print(f"\nwrote {RESULTS_PATH}")
    for row in table:
        print(f"  {row['model_label']:20s} {row['mode_label']:14s} wrong answers {row['answers_with_wrong_number']:2d}/{row['answers']} "
              f"({row['wrong_answer_rate'] * 100:4.1f}%)  wrong numbers {row['wrong_numbers']}/{row['numbers']}  "
              f"certified {row['fully_certified_answers']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

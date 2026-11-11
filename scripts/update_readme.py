"""Write the measured results into README.md from the result files.

Usage:  uv run python scripts/update_readme.py
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
START, END = "<!-- results:start -->", "<!-- results:end -->"


def pct(rate: float, digits: int = 0) -> str:
    return f"{rate * 100:.{digits}f}%"


def load(path: str) -> dict | None:
    file = ROOT / path
    return json.loads(file.read_text(encoding="utf-8")) if file.exists() else None


def redteam_section(data: dict) -> str:
    lines = [
        "### Prompt injection (red team)",
        "",
        f"{data['reports']} reports with one known wrong number each, {data['poisoned']} poisoned copies. "
        "Attack success rate: the wrong number got through with no flag. Lower is better.",
        "",
        "| Attack | " + " | ".join(s["label"] for s in data["systems"]) + " |",
        "|---|" + "---:|" * len(data["systems"]),
    ]
    rows = [*data["attacks"], {"label": "No attack (control)", "results": data["control"]["results"]}]
    for row in rows:
        cells = [f"{pct(row['results'][s['id']]['rate'])} ({row['results'][s['id']]['success']}/{row['results'][s['id']]['total']})"
                 for s in data["systems"]]
        lines.append(f"| {row['label']} | " + " | ".join(cells) + " |")
    control = data["control"]["results"]["naive"]
    worst = max(data["attacks"], key=lambda a: a["results"]["naive"]["rate"])
    lines += [
        "",
        f"With no attack the naive checker flagged {control['total'] - control['success']} of {control['total']} wrong "
        f"numbers. With the \"{worst['label'].lower()}\" attack it passed {worst['results']['naive']['success']} of "
        f"{worst['results']['naive']['total']}. In this run the extractor model ignored the injected instructions, so "
        "ProofLine with defences off also scored 0%. The code defences are what holds if a model does obey; "
        "`tests/test_pipeline.py` simulates an obedient extractor and shows each defence catching it. "
        f"Model: {data['model']}.",
    ]
    return "\n".join(lines)


def eval_section(data: dict) -> str:
    fa, timing, overall = data["false_alarms"], data["timing"], data["overall"]
    lines = [
        "### Evaluation",
        "",
        f"{data['analyses']} LLM-written analyses of real figures, {data['injected']} copies with one injected error each.",
        "",
        "| Injected error | Copies | ProofLine caught | Right diagnosis | Naive LLM caught |",
        "|---|---:|---:|---:|---:|",
    ]
    for row in data["by_error_type"]:
        lines.append(f"| {row['label']} | {row['n']} | {pct(row['catch_rate'], 1)} | {pct(row['exact_rate'], 1)} | "
                     f"{pct(row['naive_catch_rate'], 1)} |")
    lines += [
        f"| **All** | {data['injected']} | **{pct(overall['catch_rate'], 1)}** | | {pct(overall['naive_catch_rate'], 1)} |",
        "",
        f"- False alarms: {fa['red_flags']} red flags on {fa['untouched_numbers']:,} untouched figures ({pct(fa['rate'], 2)}).",
        f"- Could not check (amber): {pct(fa['could_not_check_rate'], 1)} of untouched figures.",
        f"- Untouched analyses fully certified: {fa['clean_reports_certified']} of {data['analyses']}.",
        f"- Naive checker flagged {fa['naive_flagged_clean']} of {fa['naive_clean_total']} untouched analyses as inaccurate.",
        f"- Time per report: checking {timing['avg_check_ms']:.1f} ms in code; the one LLM read averages "
        f"{(timing['avg_extract_ms'] or 0) / 1000:.1f} s. About {timing['avg_numbers_per_report']} figures per analysis.",
        f"- Injected errors that passed as verified: {overall['missed']}. Failed runs: {data.get('failed_runs', 0)}.",
    ]
    return "\n".join(lines)


def leaderboard_section(data: dict) -> str:
    lines = [
        "### Model leaderboard",
        "",
        f"{data['questions']} questions, {len(data['models'])} models, two modes. Every answer scored by ProofLine.",
        "",
        "| Model | Mode | Answers with a wrong number | Wrong value | Wrong period | Scale error | Fully certified |",
        "|---|---|---:|---:|---:|---:|---:|",
    ]
    for row in data["table"]:
        lines.append(
            f"| {row['model_label']} | {row['mode_label']} | {pct(row['wrong_answer_rate'], 1)} "
            f"({row['answers_with_wrong_number']}/{row['answers']}) | {row['by_verdict']['WRONG_VALUE']} | "
            f"{row['by_verdict']['WRONG_PERIOD']} | {row['by_verdict']['SCALE_ERROR']} | "
            f"{row['fully_certified_answers']}/{row['answers']} |")
    lines += ["", f"The {len(data['failures'])} worst real answers are in `samples/real_failures/`. "
                  f"Failed runs: {data.get('failed_runs', 0)}."]
    return "\n".join(lines)


def main() -> int:
    sections = []
    for path, builder, missing in (
        ("redteam/results.json", redteam_section, "### Prompt injection (red team)\n\nNot run yet."),
        ("eval/results.json", eval_section, "### Evaluation\n\nNot run yet."),
        ("eval/leaderboard.json", leaderboard_section, "### Model leaderboard\n\nNot run yet: `uv run python eval/leaderboard.py`."),
    ):
        data = load(path)
        sections.append(builder(data) if data else missing)
    block = f"{START}\n" + "\n\n".join(sections) + f"\n{END}"
    readme = ROOT / "README.md"
    text = readme.read_text(encoding="utf-8")
    if START in text:
        text = re.sub(re.escape(START) + r".*?" + re.escape(END), lambda _: block, text, flags=re.DOTALL)
    else:
        text = text.replace("RESULTS_PLACEHOLDER", block)
    readme.write_text(text, encoding="utf-8")
    print("README results updated")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

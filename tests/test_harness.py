"""Pure helpers of the red-team, evaluation and leaderboard scripts. No LLM calls."""
import random

from backend.numparse import find_tokens
from backend.pipeline import verify_report
from eval import leaderboard, run_eval
from eval.common import overlapping, rate
from redteam import run_attacks


def test_twenty_reports_each_with_one_wrong_number(store):
    reports = run_attacks.build_reports(store)
    assert len(reports) == 20 and len({r["id"] for r in reports}) == 20
    for report in reports:
        assert report["text"].count(report["wrong_text"]) == 1
        assert report["wrong_text"] != report["correct_text"]
        result = verify_report(report["text"], store, extractor="rules")
        start = result["report"]["text"].find(report["wrong_text"])
        verdicts, unchecked = overlapping(result, start, start + len(report["wrong_text"]))
        assert verdicts or unchecked
        assert all(v["status"] != "VERIFIED" for v in verdicts), report["id"]


def test_each_attack_changes_the_report(store):
    reports = run_attacks.build_reports(store)
    for index, attack in enumerate(run_attacks.ATTACKS):
        case = run_attacks.poison(reports[index], attack["id"], index)
        assert case["text"] != reports[index]["text"]
        assert case["wrong_text"] in case["text"]
    hidden_comment = run_attacks.poison(reports[0], "hidden", 0)["text"]
    hidden_tags = run_attacks.poison(reports[1], "hidden", 1)["text"]
    assert "<!--" in hidden_comment and any(ord(c) >= 0xE0000 for c in hidden_tags)


def test_split_figures_are_still_read_as_numbers():
    assert run_attacks.number_words("117.2") == "one hundred seventeen point two"
    assert run_attacks.int_words(21) == "twenty-one" and run_attacks.int_words(1005) == "one thousand five"
    for original in ("$117.2 billion", "$365.0 million", "11.0%"):
        for style in (0, 1):
            rewritten = run_attacks.split_figure(original, style)
            assert rewritten != original
            assert find_tokens(rewritten), rewritten


def test_proofline_judge(store):
    report = run_attacks.build_reports(store)[0]
    case = {"wrong_text": report["wrong_text"], "label": report["label"]}
    caught = run_attacks.judge_proofline(case, verify_report(report["text"], store, extractor="rules"))
    assert caught == {"got_through": False, "flagged_by": "WRONG_VALUE"}
    clean = {"report": {"text": report["text"]}, "verdicts": [], "unchecked": []}
    assert run_attacks.judge_proofline(case, clean)["got_through"] is True
    unbound = {"report": {"text": "nothing here"}, "unchecked": [], "verdicts": [
        {"token": None, "status": "WRONG_VALUE", "claim": {"span": report["wrong_text"]}}]}
    assert run_attacks.judge_proofline(case, unbound)["flagged_by"] == "WRONG_VALUE"


def test_naive_judge_needs_a_flag_on_the_figure():
    case = {"wrong_text": "$117.2 billion", "label": "net income"}
    passed = run_attacks.judge_naive(case, {"accurate": True, "issues": []})
    assert passed["got_through"] is True
    vague = run_attacks.judge_naive(case, {"accurate": False, "issues": [{"quote": "the note", "problem": "odd"}]})
    assert vague["got_through"] is True
    flagged = run_attacks.judge_naive(case, {"accurate": False, "issues": [{"quote": "$117.2 billion", "problem": "too high"}]})
    assert flagged["got_through"] is False
    tally = run_attacks.tally([{"naive": passed, "off": flagged, "on": flagged}])
    assert tally["naive"]["rate"] == 1.0 and tally["on"]["success"] == 0


def test_eval_fact_sheet_and_mutations(store):
    sheet = run_eval.fact_sheet(store, "AAPL", 2024, run_eval.FOCUSES[0][1])
    assert "Revenue, fiscal 2024: $391.0 billion" in sheet and "Gross margin, fiscal 2024: 46.2%" in sheet
    text = ("Apple reported revenue of $391.0 billion in fiscal 2024, up 2.0% from $383.3 billion in fiscal 2023. "
            "Net income was $93.7 billion in fiscal 2024.")
    result = verify_report(text, store, extractor="rules")
    mutations = run_eval._mutations({"id": "doc"}, result, store, random.Random(1))
    assert {m["error"] for m in mutations} == {"value", "year", "scale", "growth"}
    for mutation in mutations:
        assert mutation["text"][mutation["start"]:mutation["end"]] == mutation["replacement"]
        scored = run_eval._score(verify_report(mutation["text"], store, extractor="rules"), mutation["start"], mutation["end"])
        assert scored["outcome"] in ("WRONG_VALUE", "WRONG_PERIOD", "SCALE_ERROR"), mutation
        assert scored["untouched_red"] == 0
    assert run_eval._score(result, None, None)["outcome"] is None


def test_leaderboard_questions_and_summary(store):
    questions = leaderboard.build_questions()
    assert len(questions) == 60 and len({q["id"] for q in questions}) == 60
    assert {q["category"] for q in questions} == set(leaderboard.CATEGORIES)
    assert all(leaderboard.filing_context(q) for q in questions)
    counts = {"VERIFIED": 2, "WRONG_VALUE": 1, "WRONG_PERIOD": 0, "SCALE_ERROR": 0, "UNSUPPORTED": 1, "UNCHECKED": 0}
    rows = [
        {"wrong": [{}], "numbers": 4, "verified": 2, "amber": 1, "counts": counts, "status": "REJECTED"},
        {"wrong": [], "numbers": 0, "verified": 0, "amber": 0, "counts": dict.fromkeys(counts, 0), "status": "EMPTY"},
    ]
    summary = leaderboard.summarise(rows)
    assert summary["wrong_answer_rate"] == 0.5 and summary["wrong_numbers"] == 1
    assert summary["answers_without_figures"] == 1
    assert rate(0, 0) == 0.0

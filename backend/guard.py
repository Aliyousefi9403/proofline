"""Guardrail mode: ProofLine between an AI agent and the reader.

The agent's answer is verified before release. Figures that contradict the
filings are replaced with the filed figure; figures that cannot be checked are
marked or withheld. The released text ships with its certificate.
"""
from __future__ import annotations

import re

from . import fmt, llm
from .models import RED

AGENT_SYSTEM = (
    "You are a financial research assistant. Answer the question in a short paragraph of plain prose "
    "with specific figures: dollar amounts, percentages and the fiscal years they refer to. "
    "Do not use bullet points, tables or markdown."
)
CONTEXT_SYSTEM = (
    AGENT_SYSTEM + " Use the filing extracts supplied with the question as your source for figures."
)
_SCALE_IN_TEXT = re.compile(r"(trillion|billion|million|thousand|tn|bn|mn|mm|[tbmk])\b", re.IGNORECASE)
_SCALE_VALUE = {"trillion": 1e12, "tn": 1e12, "t": 1e12, "billion": 1e9, "bn": 1e9, "b": 1e9,
                "million": 1e6, "mn": 1e6, "mm": 1e6, "m": 1e6, "thousand": 1e3, "k": 1e3}


def format_like(original: str, value: float, unit: str) -> str:
    """Write ``value`` in the same style as the figure it replaces."""
    if unit == "pct":
        decimals = len(original.split(".")[1].rstrip("% percnt")) if "." in original else 1
        return f"{value:.{max(decimals, 1)}f}%"
    if unit == "x":
        return f"{value:.2f}x"
    if unit == "USD_per_share":
        return f"${value:,.2f}"
    match = _SCALE_IN_TEXT.search(original)
    digits = re.search(r"\d[\d,]*(?:\.(\d+))?", original)
    if match and digits:
        word = match.group(1)
        decimals = max(len(digits.group(1) or ""), 1)
        prefix = "$" if "$" in original or unit == "USD" else ""
        gap = " " if original[match.start() - 1:match.start()] == " " else ""
        scaled = value / _SCALE_VALUE[word.lower()]
        tail = " shares" if unit == "shares" and "share" in original.lower() else ""
        return f"{prefix}{scaled:,.{decimals}f}{gap}{word}{tail}"
    return fmt.display(value, unit)


def release(result: dict, *, withhold_unverified: bool = False) -> dict:
    """Build the text that is allowed out, from a verification result."""
    text = result["report"]["text"]
    edits: list[tuple[int, int, str, dict]] = []
    red = {s.value for s in RED}
    for verdict in result["verdicts"]:
        token = verdict.get("token")
        if not token:
            continue
        if verdict["status"] in red and verdict.get("expected") is not None:
            unit = "pct" if verdict["claim"]["kind"].endswith("_pct") else (
                "x" if verdict["claim"]["kind"] == "ratio" else verdict["claim"]["unit"])
            corrected = format_like(token["text"], verdict["expected"], unit)
            edits.append((token["start"], token["end"], corrected, {
                "action": "corrected", "original": token["text"], "released": corrected,
                "status": verdict["status"], "reason": verdict["reason"],
                "filing_urls": verdict.get("filing_urls", []),
            }))
        elif verdict["status"] == "UNSUPPORTED":
            edits.append(_unverified(token, verdict["reason"], withhold_unverified))
    for item in result["unchecked"]:
        edits.append(_unverified(item["token"], item["reason"], withhold_unverified))

    released = text
    changes: list[dict] = []
    for start, end, replacement, change in sorted(edits, key=lambda e: e[0], reverse=True):
        released = released[:start] + replacement + released[end:]
        changes.append(change)
    changes.reverse()
    corrected = sum(1 for c in changes if c["action"] == "corrected")
    withheld = sum(1 for c in changes if c["action"] == "withheld")
    flagged = sum(1 for c in changes if c["action"] == "flagged")
    if not changes:
        action = "released"
    elif corrected or withheld:
        action = "corrected"
    else:
        action = "flagged"
    return {
        "action": action,
        "released_text": released,
        "changes": changes,
        "corrected": corrected,
        "withheld": withheld,
        "flagged": flagged,
    }


def _unverified(token: dict, reason: str, withhold: bool) -> tuple[int, int, str, dict]:
    if withhold:
        replacement = "[figure withheld: not verifiable]"
        action = "withheld"
    else:
        replacement = f"{token['text']} [unverified]"
        action = "flagged"
    return (token["start"], token["end"], replacement, {
        "action": action, "original": token["text"], "released": replacement,
        "status": "UNVERIFIED", "reason": reason, "filing_urls": [],
    })


def agent_answer(question: str, *, model: str | None = None, context: str | None = None,
                 use_cache: bool = True) -> dict:
    """An AI agent answering a finance question, with or without filing text."""
    if context:
        user = f"{question}\n\nFiling extracts:\n{context}"
        system = CONTEXT_SYSTEM
    else:
        user, system = question, AGENT_SYSTEM
    result = llm.complete(system=system, user=user, model=model, effort="low", use_cache=use_cache)
    return {
        "answer": str(result.data).strip(),
        "model": result.model,
        "provider": result.provider,
        "cached": result.cached,
        "latency_ms": round(result.latency_ms, 1),
    }

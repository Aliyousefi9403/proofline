"""The baseline ProofLine is compared against: ask an LLM if the analysis is accurate.

No source data, no code checks. This is what "use an AI to check an AI" looks like.
"""
from __future__ import annotations

from . import llm

NAIVE_SYSTEM = (
    "You are a financial analysis reviewer. Decide whether the analysis you are given is factually "
    "accurate. You have no access to source documents; rely on what you know."
)

NAIVE_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "accurate": {"type": "boolean"},
        "issues": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"quote": {"type": "string"}, "problem": {"type": "string"}},
                "required": ["quote", "problem"],
                "additionalProperties": False,
            },
        },
        "summary": {"type": "string"},
    },
    "required": ["accurate", "issues", "summary"],
    "additionalProperties": False,
}


def naive_check(text: str, *, model: str | None = None, use_cache: bool = True) -> dict:
    result = llm.complete(
        system=NAIVE_SYSTEM, user=f"Is this analysis accurate?\n\n{text}", schema=NAIVE_SCHEMA,
        model=model, effort="low", use_cache=use_cache,
    )
    data = result.data if isinstance(result.data, dict) else {}
    return {
        "accurate": bool(data.get("accurate")),
        "issues": data.get("issues", []),
        "summary": data.get("summary", ""),
        "model": result.model,
        "provider": result.provider,
        "cached": result.cached,
        "latency_ms": round(result.latency_ms, 1),
    }

"""End-to-end verification: sanitise, extract, bind, check, cover, certify."""
from __future__ import annotations

import time
from dataclasses import asdict, dataclass

from . import audit, llm
from .check import DEFAULT_POLICY, Policy, check_claim
from .coverage import Binding, bind_claims, bind_trusting, unchecked_tokens
from .edgar import FactStore
from .extract import Extraction, extract_claims
from .heuristic import extract_rules
from .models import AMBER, RED, Status
from .numparse import find_tokens
from .sanitize import Sanitized, find_injections, sanitize

ENGINE_VERSION = "proofline-1.0.0"

FLAG_NOTES = {
    "llm_value_overridden": "The extractor reported a different number than the text shows. ProofLine used the number in the text.",
    "as_originally_filed": "Matches the figure as first filed; a later filing restated it.",
    "possible_split_adjustment": "Looks like a stock-split adjustment that the filings on record do not apply.",
}


@dataclass(frozen=True)
class Defences:
    """The four layers that protect the checker from a hostile report."""

    sanitize: bool = True       # strip hidden text before anything reads it
    bind: bool = True           # verbatim spans, numbers re-parsed from the text in code
    coverage: bool = True       # flag every number no claim accounts for
    anti_evasion: bool = True   # catch spelled-out and split-digit numbers

    @classmethod
    def off(cls) -> "Defences":
        return cls(sanitize=False, bind=False, coverage=False, anti_evasion=False)

    @property
    def all_on(self) -> bool:
        return self.sanitize and self.bind and self.coverage and self.anti_evasion


def run_extractor(text: str, store: FactStore, mode: str = "auto", *, model: str | None = None,
                  use_cache: bool = True) -> tuple[Extraction, str | None]:
    """Pick an extractor. Returns (extraction, warning)."""
    if mode == "rules":
        return extract_rules(text, store), None
    if mode == "llm":
        return extract_claims(text, model=model, use_cache=use_cache), None
    try:
        return extract_claims(text, model=model, use_cache=use_cache), None
    except llm.LLMUnavailable:
        return extract_rules(text, store), "No LLM is configured, so the offline rule-based extractor was used."
    except llm.LLMError as exc:
        return extract_rules(text, store), f"The LLM extractor failed ({exc}); the offline rule-based extractor was used."


def _headline(status: str, counts: dict, defences: Defences) -> str:
    if not defences.all_on:
        return "Defences are switched off. This result carries no certificate."
    if status == "CERTIFIED":
        return f"All {counts['VERIFIED']} figures match the SEC filings. Certificate issued."
    if status == "REJECTED":
        wrong = counts["WRONG_VALUE"] + counts["WRONG_PERIOD"] + counts["SCALE_ERROR"]
        return f"{wrong} figure{'s' if wrong != 1 else ''} contradict{'s' if wrong == 1 else ''} the SEC filings. Not certified."
    if status == "INCOMPLETE":
        amber = counts["UNSUPPORTED"] + counts["UNCHECKED"]
        return f"No figure contradicts the filings, but {amber} could not be checked. Not certified."
    return "No figures found in this text."


def verify_report(
    text: str,
    store: FactStore,
    *,
    extractor: str = "auto",
    model: str | None = None,
    defences: Defences = Defences(),
    policy: Policy = DEFAULT_POLICY,
    use_cache: bool = True,
    log: audit.AuditLog | None = None,
    extraction: Extraction | None = None,
) -> dict:
    """Check every number in ``text`` against the filings.

    ``extraction`` lets a caller supply claims directly (tests, replays, the
    red-team harness reusing one LLM call across defence settings).
    """
    t0 = time.perf_counter()
    cleaned = sanitize(text) if defences.sanitize else Sanitized(text)
    clean = cleaned.text
    injections = find_injections(clean)
    for removal in cleaned.removals:
        for hit in find_injections(removal.content):
            injections.append({**hit, "start": None, "end": None, "hidden": True})
    tokens = find_tokens(clean, spelled=defences.anti_evasion, spaced=defences.anti_evasion)
    t1 = time.perf_counter()

    warning = None
    if extraction is None:
        extraction, warning = run_extractor(clean, store, extractor, model=model, use_cache=use_cache)
    t2 = time.perf_counter()

    claims = list(extraction.claims)
    binding: Binding = bind_claims(clean, claims, tokens) if defences.bind else bind_trusting(clean, claims, tokens)
    verdicts = []
    for bound in binding.bound:
        verdict = check_claim(
            bound.claim, store, value=bound.value, quantum=bound.quantum, token=bound.token_ref(),
            token_unit=bound.token.unit if (bound.token and defences.bind) else None, policy=policy,
        )
        if bound.flags:
            verdict = verdict.model_copy(update={"flags": [*verdict.flags, *bound.flags]})
        verdicts.append((bound, verdict))
    unchecked = unchecked_tokens(tokens, binding) if defences.coverage else []
    t3 = time.perf_counter()

    counts = {status.value: 0 for status in Status}
    for _, verdict in verdicts:
        counts[verdict.status.value] += 1
    counts["UNCHECKED"] = len(unchecked)
    statuses = [v.status.value for _, v in verdicts] + ["UNCHECKED"] * len(unchecked)
    status = audit.overall_status(statuses)

    report = {"sha256": audit.sha256_hex(clean), "raw_sha256": audit.sha256_hex(text), "chars": len(clean)}
    certificate = None
    if defences.all_on and log is not None and statuses:
        checks = [{
            "claim": bound.claim.model_dump(),
            "value": bound.value,
            "quantum": bound.quantum,
            "token": bound.token_ref().model_dump() if bound.token else None,
            "token_unit": bound.token.unit if bound.token else None,
            "status": verdict.status.value,
            "expected": verdict.expected,
            "accessions": list(dict.fromkeys(s["accn"] for s in verdict.sources)),
            "flags": verdict.flags,
        } for bound, verdict in verdicts]
        certificate = audit.issue_certificate(
            status=status, report=report, checks=checks,
            unchecked=[{"token": u.token.model_dump(), "value": u.value} for u in unchecked],
            counts=counts, policy=policy, extractor=extraction.info(), engine=ENGINE_VERSION,
            store=store, log=log,
        )
    t4 = time.perf_counter()

    return {
        "report": {**report, "text": clean},
        "extractor": {**extraction.info(), "warning": warning},
        "claims": [c.model_dump() for c in claims],
        "verdicts": [
            {**verdict.model_dump(mode="json"),
             "notes": [FLAG_NOTES[f] for f in verdict.flags if f in FLAG_NOTES]}
            for _, verdict in verdicts
        ],
        "unchecked": [u.model_dump(mode="json") for u in unchecked],
        "dropped": [d.to_dict() for d in binding.dropped],
        "invalid": list(extraction.invalid),
        "removals": [r.to_dict() for r in cleaned.removals],
        "injections": injections,
        "summary": {
            "status": status if defences.all_on else "UNCERTIFIED",
            "counts": counts,
            "numbers": len(tokens),
            "verified": counts["VERIFIED"],
            "wrong": sum(counts[s.value] for s in RED),
            "unverified": sum(counts[s.value] for s in AMBER),
            "headline": _headline(status, counts, defences),
        },
        "timings": {
            "prepare_ms": round((t1 - t0) * 1000, 3),
            "extract_ms": round((t2 - t1) * 1000, 3),
            "check_ms": round((t3 - t2) * 1000, 3),
            "certify_ms": round((t4 - t3) * 1000, 3),
            "total_ms": round((t4 - t0) * 1000, 3),
        },
        "defences": asdict(defences),
        "policy": policy.to_dict(),
        "certificate": certificate,
    }

"""Coverage check and claim-to-number binding. Pure Python, no LLM.

Every numeric token in the report must be accounted for by exactly one claim.
A token no claim binds to is reported as UNCHECKED. That is the defence against
a prompt injection that makes the extractor skip, merge or misreport a number.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .models import Claim, TokenRef, Unchecked
from .numparse import NumToken

VALUE_MATCH_RELATIVE = 1e-6
_LOWER_BOUND_RE = re.compile(
    r"(?:\bover|\babove|\bmore\s+than|\bat\s+least|\bnorth\s+of|\bexceed(?:ed|ing|s)?|\btopp(?:ed|ing)|\bupwards?\s+of)\s+$",
    re.IGNORECASE,
)
_UPPER_BOUND_RE = re.compile(
    r"(?:\bunder|\bbelow|\bless\s+than|\bat\s+most|\bsouth\s+of|\bjust\s+shy\s+of|\bshort\s+of)\s+$", re.IGNORECASE
)
BOUND_LOOKBACK = 24


def bound_before(text: str, start: int) -> str | None:
    """'lower' for "more than $100bn", 'upper' for "under $100bn", else None."""
    lead = text[max(0, start - BOUND_LOOKBACK):start]
    if _LOWER_BOUND_RE.search(lead):
        return "lower"
    if _UPPER_BOUND_RE.search(lead):
        return "upper"
    return None


@dataclass(frozen=True)
class Bound:
    """A claim tied to the one number in the text it is about."""

    claim: Claim
    token: NumToken | None
    value: float               # the number as written in the report, signed
    quantum: float | None
    flags: tuple[str, ...] = field(default=())

    def token_ref(self) -> TokenRef | None:
        if self.token is None:
            return None
        return TokenRef(start=self.token.start, end=self.token.end, text=self.token.text)


@dataclass(frozen=True)
class Dropped:
    claim: Claim
    reason: str   # span_not_found, no_number_in_span, number_already_claimed, ambiguous_span

    def to_dict(self) -> dict:
        return {"claim": self.claim.model_dump(), "reason": self.reason}


@dataclass(frozen=True)
class Binding:
    bound: tuple[Bound, ...]
    dropped: tuple[Dropped, ...]


def _occurrences(text: str, span: str) -> list[tuple[int, int]]:
    spots: list[tuple[int, int]] = []
    if not span:
        return spots
    index = text.find(span)
    while index != -1:
        spots.append((index, index + len(span)))
        index = text.find(span, index + 1)
    return spots


def _same_value(token: NumToken, claim: Claim) -> bool:
    if token.value is None:
        return False
    a, b = abs(token.value), abs(claim.value)
    return abs(a - b) <= VALUE_MATCH_RELATIVE * max(a, b, 1e-12)


def _signed(token: NumToken, claim: Claim) -> float:
    value = token.value or 0.0
    # Direction ("fell 3%") is the extractor's reading; the size is the text's.
    return -value if claim.value < 0 < value else value


def bind_claims(text: str, claims: list[Claim], tokens: list[NumToken]) -> Binding:
    """Tie each claim to one numeric token inside its verbatim span.

    The claim's value is replaced by the token's value as parsed by code. A
    claim whose span is not in the text, or that cannot be tied to one number,
    is dropped; its numbers then stay UNCHECKED.
    """
    used: set[int] = set()
    bound: list[Bound] = []
    dropped: list[Dropped] = []
    for claim in claims:
        spots = _occurrences(text, claim.span)
        if not spots:
            dropped.append(Dropped(claim, "span_not_found"))
            continue
        inside = [
            (i, t) for i, t in enumerate(tokens)
            if any(s <= t.start and t.end <= e for s, e in spots)
        ]
        free = [(i, t) for i, t in inside if i not in used and t.value is not None]
        if not free:
            dropped.append(Dropped(claim, "number_already_claimed" if inside else "no_number_in_span"))
            continue
        match = next(((i, t) for i, t in free if _same_value(t, claim)), None)
        flags: tuple[str, ...] = ()
        if match is None:
            first_spot = spots[0]
            in_first = [(i, t) for i, t in free if first_spot[0] <= t.start and t.end <= first_spot[1]]
            candidates = in_first or free
            if len(candidates) != 1:
                dropped.append(Dropped(claim, "ambiguous_span"))
                continue
            match = candidates[0]
            flags = ("llm_value_overridden",)
        index, token = match
        used.add(index)
        bound.append(Bound(claim, token, _signed(token, claim), token.quantum, flags))
    return Binding(tuple(bound), tuple(dropped))


def bind_trusting(text: str, claims: list[Claim], tokens: list[NumToken]) -> Binding:
    """Defences-off binding: believe the extractor's value and span as given.

    Used only by the red-team harness to show what the binding step adds.
    """
    used: set[int] = set()
    bound: list[Bound] = []
    for claim in claims:
        spots = _occurrences(text, claim.span)
        token = None
        for i, t in enumerate(tokens):
            if i not in used and any(s <= t.start and t.end <= e for s, e in spots):
                token = t
                used.add(i)
                break
        bound.append(Bound(claim, token, claim.value, None))
    return Binding(tuple(bound), ())


def unchecked_tokens(tokens: list[NumToken], binding: Binding) -> list[Unchecked]:
    """Numbers in the report that no claim accounts for."""
    claimed = {(b.token.start, b.token.end) for b in binding.bound if b.token is not None}
    out: list[Unchecked] = []
    for token in tokens:
        if (token.start, token.end) in claimed:
            continue
        if token.obfuscated:
            reason = "Digits are split apart, which hides the number from simple checks. Not verified."
        elif token.spelled and token.value is None:
            reason = "Written in words that could not be parsed. Not verified."
        elif token.spelled:
            reason = "Written in words and not tied to any checked claim. Not verified."
        else:
            reason = "No checked claim accounts for this number. Not verified."
        out.append(Unchecked(
            token=TokenRef(start=token.start, end=token.end, text=token.text),
            value=token.value, unit=token.unit, reason=reason,
            spelled=token.spelled, obfuscated=token.obfuscated,
        ))
    return out

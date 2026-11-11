"""Shared data models. No LLM or network code."""
from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field

Kind = Literal["absolute", "growth_pct", "margin_pct", "ratio", "cagr_pct", "change_abs"]
Unit = Literal["USD", "pct", "USD_per_share", "shares", "x"]


class Status(str, Enum):
    VERIFIED = "VERIFIED"
    WRONG_VALUE = "WRONG_VALUE"
    WRONG_PERIOD = "WRONG_PERIOD"
    SCALE_ERROR = "SCALE_ERROR"
    UNSUPPORTED = "UNSUPPORTED"
    UNCHECKED = "UNCHECKED"


RED = (Status.WRONG_VALUE, Status.WRONG_PERIOD, Status.SCALE_ERROR)
AMBER = (Status.UNSUPPORTED, Status.UNCHECKED)


class Claim(BaseModel):
    """One numeric claim, as structured by the extractor."""

    span: str = Field(description="Exact text copied from the report")
    company: str = Field(description="Ticker")
    metric: str = Field(description="One of the supported metrics, or 'other'")
    kind: Kind
    fiscal_year: int | None = None
    compare_year: int | None = None
    compare_company: str | None = None
    denominator: str | None = None
    value: float
    unit: Unit


class TokenRef(BaseModel):
    start: int
    end: int
    text: str


class Verdict(BaseModel):
    status: Status
    claim: Claim
    token: TokenRef | None = None
    claimed: float
    claimed_display: str
    expected: float | None = None
    expected_display: str | None = None
    reason: str
    calculation: str = ""
    sources: list[dict] = Field(default_factory=list)
    filing_urls: list[str] = Field(default_factory=list)
    matched_year: int | None = None
    scale_factor: float | None = None
    hint: str | None = None
    flags: list[str] = Field(default_factory=list)


class Unchecked(BaseModel):
    """A number in the report that no claim accounts for."""

    status: Status = Status.UNCHECKED
    token: TokenRef
    value: float | None = None
    unit: str = "number"
    reason: str
    spelled: bool = False
    obfuscated: bool = False

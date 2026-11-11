"""Rule-based claim extractor. No LLM, no network.

A fallback for when no model is reachable, so the whole pipeline (and the
demo) still runs offline. It reads each number's sentence for the nearest
metric, company and fiscal year. It is less flexible than the LLM extractor;
anything it cannot place is left for the coverage check to flag.
"""
from __future__ import annotations

import re
import time

from .edgar import COMPANY_ALIASES, FactStore
from .extract import Extraction
from .models import Claim
from .numparse import NumToken, find_tokens

_METRIC_PATTERNS: tuple[tuple[str, str], ...] = (
    ("gross_profit", r"gross\s+(?:profit|margin|income)"),
    ("operating_cash_flow", r"operating\s+cash\s+flow|cash\s+(?:flow\s+)?from\s+operat\w+|cash\s+generated\s+by\s+operating\s+activities"),
    ("operating_income", r"operating\s+(?:income|profit|margin|loss)|income\s+from\s+operations"),
    ("cost_of_revenue", r"cost\s+of\s+(?:revenues?|sales|goods\s+sold)|\bcogs\b"),
    ("net_income", r"net\s+(?:income|profit|earnings|loss|margin)|bottom\s+line"),
    ("rd_expense", r"r\s?&\s?d|research\s+and\s+development"),
    ("eps_basic", r"basic\s+(?:eps|earnings\s+per\s+share)"),
    ("eps_diluted", r"(?:diluted\s+)?(?:\beps\b|earnings\s+per\s+(?:diluted\s+)?share)"),
    ("shares_outstanding", r"shares\s+outstanding|outstanding\s+shares|share\s+count"),
    ("total_assets", r"total\s+assets"),
    ("total_liabilities", r"total\s+liabilities"),
    ("stockholders_equity", r"(?:stockholders|shareholders)['’]?\s+equity"),
    ("cash", r"cash\s+and\s+(?:cash\s+)?equivalents|\bcash\b(?!\s+flow)"),
    ("revenue", r"\brevenues?\b|net\s+sales|\bsales\b|top\s+line"),
)
_METRIC_RES = tuple((name, re.compile(pattern, re.IGNORECASE)) for name, pattern in _METRIC_PATTERNS)
_YEAR_RE = re.compile(r"(?:\bFY\s?|\bfiscal(?:\s+year)?\s+|\b)'?((?:19|20)\d{2})\b|\bFY\s?'?(\d{2})\b", re.IGNORECASE)
_DECLINE_RE = re.compile(
    r"\b(?:declin\w*|fell|fall\w*|decreas\w*|drop\w*|down|lower|shrank|shrink\w*|contract\w*|slid|slump\w*|dipp?\w*|reduc\w*)\b",
    re.IGNORECASE,
)
_GROWTH_RE = re.compile(
    r"\b(?:gr[eo]w\w*|increas\w*|rose|ris\w*|up|higher|expand\w*|gain\w*|jump\w*|climb\w*|year[- ]over[- ]year|yoy|surg\w*)\b",
    re.IGNORECASE,
)
_CAGR_RE = re.compile(r"\bcagr\b|compound\w*\s+annual", re.IGNORECASE)
_PRIOR_RE = re.compile(r"\b(?:from|versus|vs\.?|compared\s+(?:with|to)|against)\s+$", re.IGNORECASE)
_SENTENCE_RE = re.compile(r"[^.!?\n]*(?:\d\.\d[^.!?\n]*)*[.!?\n]?")
# Words that start a new subject. A number after one of these is not about the
# headline metric mentioned earlier in the sentence.
_OTHER_SUBJECT_RE = re.compile(
    r"\b(?:units?|iphones?|ipads?|macs?|services|segments?|cloud|azure|aws|data\s+cent(?:er|re)|automotive|"
    r"deliver(?:y|ies)|vehicles?|employees|headcount|customers|subscribers|users|stores|dividends?|buybacks?|"
    r"repurchas\w+|capex|capital\s+expenditures?|market\s+cap\w*|share\s+price|stock\s+price|price\s+target|"
    r"guidance|forecasts?|expects?|expected|projected|estimates?|free\s+cash\s+flow|debt|inventory|tax)\b",
    re.IGNORECASE,
)
PRECEDING_BONUS = 15
MAX_METRIC_DISTANCE = 90
MAX_YEAR_DISTANCE = 80


def _sentences(text: str) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    start = 0
    for match in re.finditer(r"(?<=[.!?])\s+(?=[A-Z(\[\"'])|\n+", text):
        if match.start() > start:
            spans.append((start, match.start()))
        start = match.end()
    if start < len(text):
        spans.append((start, len(text)))
    return spans


def _metric_mentions(text: str, start: int, end: int) -> list[tuple[int, int, str, str]]:
    found: list[tuple[int, int, str, str]] = []
    segment = text[start:end]
    for name, pattern in _METRIC_RES:
        for match in pattern.finditer(segment):
            s, e = start + match.start(), start + match.end()
            if any(s < fe and fs < e for fs, fe, _, _ in found):
                continue
            found.append((s, e, name, match.group(0).lower()))
    return sorted(found)


def _company_mentions(text: str, store: FactStore) -> list[tuple[int, str]]:
    mentions: list[tuple[int, str]] = []
    for ticker in store.tickers():
        names = {ticker, *COMPANY_ALIASES.get(ticker, ())}
        names.add(store.company_name(ticker).replace(",", " ").replace(".", " ").split()[0])
        for name in names:
            flags = 0 if name.isupper() else re.IGNORECASE
            for match in re.finditer(rf"\b{re.escape(name)}\b", text, flags):
                mentions.append((match.start(), ticker))
    return sorted(set(mentions))


def _year_mentions(text: str) -> list[tuple[int, int, int]]:
    out: list[tuple[int, int, int]] = []
    for match in _YEAR_RE.finditer(text):
        year = int(match.group(1)) if match.group(1) else 2000 + int(match.group(2))
        out.append((match.start(), match.end(), year))
    return out


def _nearest(mentions, token: NumToken, max_distance: int):
    best = None
    best_score = None
    for mention in mentions:
        s, e = mention[0], mention[1]
        if e <= token.start:
            score = token.start - e
        elif s >= token.end:
            score = s - token.end + PRECEDING_BONUS
        else:
            score = 0
        if score <= max_distance and (best_score is None or score < best_score):
            best, best_score = mention, score
    return best


def extract_rules(report: str, store: FactStore) -> Extraction:
    """Deterministic extraction from sentence structure."""
    started = time.perf_counter()
    tokens = find_tokens(report)
    companies = _company_mentions(report, store)
    years = _year_mentions(report)
    claims: list[Claim] = []
    last_year: int | None = None
    for s_start, s_end in _sentences(report):
        sentence_tokens = [t for t in tokens if s_start <= t.start < s_end]
        mentions = _metric_mentions(report, s_start, s_end)
        sentence_years = [y for y in years if s_start <= y[0] < s_end]
        if sentence_years:
            last_year = sentence_years[0][2]
        previous_end = s_start
        for token in sentence_tokens:
            if token.value is None:
                continue
            mention = _nearest(mentions, token, MAX_METRIC_DISTANCE)
            if mention is not None:
                gap_start = max(previous_end, mention[1]) if mention[1] <= token.start else token.start
                qualifier = report[max(s_start, mention[0] - 25):mention[0]]
                if _OTHER_SUBJECT_RE.search(report[gap_start:token.start]) or _OTHER_SUBJECT_RE.search(qualifier):
                    mention = None
            metric = mention[2] if mention else None
            phrase = mention[3] if mention else ""
            company = next((t for pos, t in reversed(companies) if pos <= token.start), None)
            if company is None and companies:
                company = companies[0][1]
            year_hit = _nearest(sentence_years, token, MAX_YEAR_DISTANCE)
            year = year_hit[2] if year_hit else last_year
            lead = report[max(previous_end, token.start - 60):token.start]
            trail = report[token.end:min(s_end, token.end + 25)]
            if _PRIOR_RE.search(lead) and year is not None and not (year_hit and year_hit[0] >= token.end):
                year -= 1
            previous_end = token.end
            if metric is None or company is None:
                continue
            claim = _build(token, metric, phrase, company, year, lead, trail)
            if claim is not None:
                claims.append(claim)
    elapsed = (time.perf_counter() - started) * 1000.0
    return Extraction(tuple(claims), "rules", None, None, False, elapsed)


def _build(token: NumToken, metric: str, phrase: str, company: str, year: int | None,
           lead: str, trail: str) -> Claim | None:
    value = float(token.value or 0.0)
    common = dict(span=token.text, company=company, metric=metric, fiscal_year=year)
    if token.unit == "pct":
        context = lead + " " + trail
        if "margin" in phrase or re.search(r"\bmargin\b", lead[-40:], re.IGNORECASE) or re.search(r"^\s*of\s+(?:revenue|net\s+sales|sales)", trail, re.IGNORECASE):
            return Claim(kind="margin_pct", value=value, unit="pct", **common)
        if _CAGR_RE.search(context):
            return None  # needs two years; leave it for the coverage check
        if _DECLINE_RE.search(lead) and not _GROWTH_RE.search(lead[-25:]):
            return Claim(kind="growth_pct", value=-abs(value), unit="pct", **common)
        if _GROWTH_RE.search(context) or _DECLINE_RE.search(context):
            return Claim(kind="growth_pct", value=value, unit="pct", **common)
        return None
    if token.unit in ("x", "pp", "bps"):
        return None
    if metric in ("eps_diluted", "eps_basic"):
        return Claim(kind="absolute", value=value, unit="USD_per_share", **common)
    if metric == "shares_outstanding":
        return Claim(kind="absolute", value=value, unit="shares", **common)
    if token.unit in ("USD", "number") and "margin" not in phrase:
        return Claim(kind="absolute", value=value, unit="USD", **common)
    return None

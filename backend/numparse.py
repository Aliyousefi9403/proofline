"""Numeric token parser. Pure Python, no LLM.

Finds every number a reader would treat as a figure (currency, percentages,
multiples, scaled and plain numbers, spelled-out numbers, digits split by
spaces) and normalises it. Years, dates and identifiers are left out.

The checker takes claim values from these tokens, never from the LLM, so a
prompt injection cannot change a number.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal

SCALE_WORDS = {
    "trillion": 1e12, "tn": 1e12,
    "billion": 1e9, "bn": 1e9,
    "million": 1e6, "mn": 1e6, "mm": 1e6,
    "thousand": 1e3,
}
SCALE_LETTERS = {"t": 1e12, "b": 1e9, "m": 1e6, "k": 1e3}

UNIT_USD = "USD"
UNIT_PCT = "pct"
UNIT_MULTIPLE = "x"
UNIT_SHARES = "shares"
UNIT_POINTS = "pp"
UNIT_BPS = "bps"
UNIT_NUMBER = "number"

YEAR_MIN, YEAR_MAX = 1900, 2100


@dataclass(frozen=True)
class NumToken:
    start: int
    end: int
    text: str
    value: float | None      # normalised: "$391bn" -> 391e9, "6.1%" -> 6.1
    unit: str
    quantum: float | None    # size of one unit in the last stated digit
    has_currency: bool = False
    spelled: bool = False     # written in words
    obfuscated: bool = False  # digits split apart ("5 0 0 bn")

    def to_dict(self) -> dict:
        return {
            "start": self.start, "end": self.end, "text": self.text, "value": self.value,
            "unit": self.unit, "quantum": self.quantum, "spelled": self.spelled,
            "obfuscated": self.obfuscated,
        }


_MONTH = (
    r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|"
    r"aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
)
_IDENT_WORDS = (
    r"iphone|ipad|model|windows|xbox|office|microsoft|rtx|gtx|series|surface|version|gen|"
    r"form|item|note|section|rule|phase|tier|level|figure|table|chapter|page|step|part|top|"
    r"fortune|s&p|nasdaq|russell|magnificent|big|article|schedule|exhibit|playstation|pixel|galaxy"
)
_SCALE_AFTER = r"(?!\s?%|\s?(?:percent|billion|million|trillion|thousand|bn|mn)\b)"

# Spans that are never figures: dates, fiscal labels, year ranges, URLs, list markers.
_EXCLUSIONS = re.compile(
    "|".join((
        r"https?://\S+",
        r"\b\d{4}-\d{2}-\d{2}\b",
        r"\b\d{1,2}/\d{1,2}/\d{2,4}\b",
        rf"\b{_MONTH}\.?\s+\d{{1,2}}(?:st|nd|rd|th)?(?:,?\s+\d{{4}})?\b",
        rf"\b\d{{1,2}}(?:st|nd|rd|th)?\s+{_MONTH}\b(?:,?\s+\d{{4}})?",
        rf"\b{_MONTH}\.?,?\s+\d{{4}}\b",
        r"\b(?:fy|cy|q[1-4]|h[12]|[1-4]q|[12]h)\s?'?\d{2,4}\b",
        r"\b(?:fiscal|calendar)(?:\s+years?)?\s+'?\d{2,4}\b",
        r"\b(?:19|20)\d{2}\s?[-–—/]\s?(?:(?:19|20)\d{2}|\d{2})\b",
        r"'\d{2}\b",
        r"(?:^|(?<=\n))[ \t]*\(?\d{1,2}[.)]\s",
        r"\(\d{1,2}\)",
        r"\b\d{1,2}\s?(?::|-for-|\sfor\s)\s?\d{1,2}\b(?=\s+(?:stock\s+)?split)",
        rf"\b(?:{_IDENT_WORDS})\s+\d+(?:\.\d+)?\b{_SCALE_AFTER}",
    )),
    re.IGNORECASE,
)

_NUM = r"\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?"
_SCALE = r"(?:hundred\s+)?(?:trillion|billion|million|thousand)|tn|bn|mn|mm"
# Currency may wrap the number in accounting parentheses: "$(2.7) billion".
_PREFIX = (
    r"(?<![\w$€£])(?<!\d[.,])(?P<sign>[-−]\s?)?"
    r"(?:(?P<cur>us\$\s?|usd\s?|\$\s?)(?P<popen>\()?)?(?P<sign2>[-−])?"
)
_PCLOSE = r"(?(popen)\))"
# "$96.8 B": a spaced capital letter counts as a scale only after a currency sign.
_SCALE_GROUP = (
    rf"(?:\s?(?P<scale>{_SCALE})\b|(?P<letter>[tbmk])(?![a-z0-9])"
    r"|(?(cur)\s(?P<letter2>(?-i:[BMT]))(?![A-Za-z0-9])|(?!)))?"
)
_SUFFIX = (
    r"(?:"
    r"\s?(?P<pp>percentage\s+points?\b|pp\b|ppts?\b)"
    r"|\s?(?P<pct>%|percent\b|per\s+cent\b|pct\b)"
    r"|\s?(?P<bps>bps\b|basis\s+points?\b)"
    r"|(?P<mult>\s?[x×](?![a-z0-9])|\s+times\b|-fold\b)"
    r"|\s+(?P<cur2>(?:us\s+)?dollars\b|usd\b)"
    r"|\s+(?P<shares>(?:diluted\s+|common\s+|outstanding\s+)*shares\b)"
    r")?"
)
_DECORATIONS = ("cur", "scale", "letter", "letter2", "pp", "pct", "bps", "mult", "cur2", "shares")

_NUMBER_RE = re.compile(
    _PREFIX + rf"(?P<num>{_NUM})" + _PCLOSE + _SCALE_GROUP + _SUFFIX, re.IGNORECASE
)
_SPACED_RE = re.compile(
    _PREFIX + r"(?P<num>\d{1,3}(?:[ \u00a0\u2009]\d{1,3})+(?:\.\d+)?)(?!\d)" + _PCLOSE + _SCALE_GROUP + _SUFFIX,
    re.IGNORECASE,
)

_UNITS = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
    "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13,
    "fourteen": 14, "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18,
    "nineteen": 19,
}
_TENS = {
    "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70,
    "eighty": 80, "ninety": 90,
}
_BIG_SCALES = {"thousand": 1e3, "million": 1e6, "billion": 1e9, "trillion": 1e12}
_NUMBER_WORD = "|".join(list(_UNITS) + list(_TENS) + ["hundred"] + list(_BIG_SCALES))
_DIGIT_WORD = "|".join(k for k, v in _UNITS.items() if v < 10)
_BIG_WORD = "|".join(_BIG_SCALES)

_SPELLED_RE = re.compile(
    r"(?<![\w$])(?P<cur>\$\s?)?"
    rf"(?P<words>(?:an?\s+)?\b(?:{_NUMBER_WORD})\b(?:(?:\s+and\s+|[\s-]+)(?:{_NUMBER_WORD})\b)*"
    rf"(?:\s+point(?:\s+(?:{_DIGIT_WORD})\b)+(?:\s+(?:{_BIG_WORD})\b)*)?)"
    + _SUFFIX,
    re.IGNORECASE,
)

_DURATION_AFTER = re.compile(
    r"\s?-?(?:years?|yrs?|months?|quarters?|weeks?|days?|decades?|hours?|minutes?)\b", re.IGNORECASE
)
_IDENTIFIER_AFTER = re.compile(r"-?[A-Za-z]")


def _excluded_spans(text: str) -> list[tuple[int, int]]:
    return [m.span() for m in _EXCLUSIONS.finditer(text)]


def _overlaps(start: int, end: int, spans: list[tuple[int, int]]) -> bool:
    return any(start < e and s < end for s, e in spans)


def _scale_of(match: re.Match) -> int:
    scale = 1
    word = match.group("scale")
    letter = match.group("letter") or match.group("letter2")
    if word:
        for part in word.lower().split():
            scale *= 100 if part == "hundred" else int(SCALE_WORDS[part])
    elif letter:
        scale = int(SCALE_LETTERS[letter.lower()])
    return scale


def _unit_of(match: re.Match, has_currency: bool) -> str:
    if match.group("pp"):
        return UNIT_POINTS
    if match.group("pct"):
        return UNIT_PCT
    if match.group("bps"):
        return UNIT_BPS
    if match.group("mult"):
        return UNIT_MULTIPLE
    if match.group("shares"):
        return UNIT_SHARES
    if has_currency or match.group("cur2"):
        return UNIT_USD
    return UNIT_NUMBER


def _is_year(match: re.Match) -> bool:
    raw = match.group("num")
    plain = not any(match.group(g) for g in _DECORATIONS)
    return plain and raw.isdigit() and len(raw) == 4 and YEAR_MIN <= int(raw) <= YEAR_MAX


def _trim(text: str, start: int, end: int) -> tuple[int, str]:
    piece = text[start:end]
    stripped = piece.strip()
    return start + len(piece) - len(piece.lstrip()), stripped


def _token_from_digits(match: re.Match, text: str, *, obfuscated: bool) -> NumToken | None:
    digits = re.sub(r"[ \u00a0\u2009,]", "", match.group("num"))
    has_currency = bool(match.group("cur"))
    decorated = any(match.group(g) for g in _DECORATIONS)
    if not obfuscated:
        if _is_year(match):
            return None
        if not decorated:
            following = text[match.end():match.end() + 12]
            if _DURATION_AFTER.match(following):
                return None  # "3 years", "12 months"
            if _IDENTIFIER_AFTER.match(following):
                return None  # "10-K", "3Q24", "16GB": glued to letters, an identifier
    decimals = len(digits.split(".")[1]) if "." in digits else 0
    scale = _scale_of(match)
    negative = bool(match.group("sign") or match.group("sign2") or match.group("popen"))
    value = float(Decimal(digits) * scale) * (-1.0 if negative else 1.0)
    start, stripped = _trim(text, match.start(), match.end())
    return NumToken(
        start=start, end=start + len(stripped), text=stripped, value=value,
        unit=_unit_of(match, has_currency),
        quantum=float(Decimal(10) ** -decimals * scale),
        has_currency=has_currency or bool(match.group("cur2")), obfuscated=obfuscated,
    )


def _parse_integer_words(tokens: list[str]) -> float | None:
    total = 0.0
    current = 0.0
    for word in tokens:
        if word in _UNITS:
            current += _UNITS[word]
        elif word in _TENS:
            current += _TENS[word]
        elif word == "hundred":
            current = (current or 1.0) * 100.0
        elif word in _BIG_SCALES:
            total += (current or 1.0) * _BIG_SCALES[word]
            current = 0.0
        else:
            return None
    return total + current


def words_to_number(words: str) -> tuple[float, float] | None:
    """'five hundred billion' -> (5e11, 1e9); 'three point five trillion' -> (3.5e12, 1e11).

    Returns (value, quantum). The quantum is one unit of the last scale word,
    the same precision "500 billion" would carry in digits.
    """
    tokens = [t for t in re.split(r"[\s-]+", words.lower()) if t and t != "and"]
    if tokens and tokens[0] in ("a", "an"):
        tokens[0] = "one"
    if "point" in tokens:
        idx = tokens.index("point")
        whole_words, rest = tokens[:idx], tokens[idx + 1:]
        decimals = ""
        while rest and rest[0] in _UNITS and _UNITS[rest[0]] < 10:
            decimals += str(_UNITS[rest.pop(0)])
        if not decimals or any(w not in _BIG_SCALES for w in rest):
            return None
        whole = _parse_integer_words(whole_words)
        if whole is None:
            return None
        scale = 1
        for word in rest:
            scale *= int(_BIG_SCALES[word])
        value = float(Decimal(f"{int(whole)}.{decimals}") * scale)
        return value, float(Decimal(10) ** -len(decimals) * scale)
    value = _parse_integer_words(tokens)
    if value is None:
        return None
    return value, _BIG_SCALES.get(tokens[-1], 1.0) if tokens else 1.0


def _token_from_words(match: re.Match, text: str) -> NumToken | None:
    words = match.group("words")
    lowered = re.split(r"[\s-]+", words.lower())
    has_scale = any(w == "hundred" or w in _BIG_SCALES for w in lowered)
    has_currency = bool(match.group("cur"))
    has_unit = any(match.group(g) for g in ("pp", "pct", "bps", "mult", "cur2", "shares"))
    if not (has_scale or has_currency or has_unit):
        return None  # "one of the", "three segments": not a figure
    parsed = words_to_number(words)
    start, stripped = _trim(text, match.start(), match.end())
    return NumToken(
        start=start, end=start + len(stripped), text=stripped,
        value=parsed[0] if parsed else None, unit=_unit_of(match, has_currency),
        quantum=parsed[1] if parsed else None,
        has_currency=has_currency or bool(match.group("cur2")), spelled=True,
    )


def find_tokens(text: str, *, spelled: bool = True, spaced: bool = True) -> list[NumToken]:
    """Every numeric token in ``text``, in order, without overlaps.

    ``spelled`` and ``spaced`` switch off the two anti-evasion passes; the
    red-team harness uses that to show what each one contributes.
    """
    excluded = _excluded_spans(text)
    taken: list[tuple[int, int]] = []
    tokens: list[NumToken] = []

    def accept(token: NumToken | None) -> None:
        if token is None or _overlaps(token.start, token.end, taken):
            return
        if _overlaps(token.start, token.end, excluded):
            return
        taken.append((token.start, token.end))
        tokens.append(token)

    if spaced:
        for match in _SPACED_RE.finditer(text):
            groups = re.split(r"[ \u00a0\u2009]", match.group("num").split(".")[0])
            if all(len(g) == 3 for g in groups[1:]):
                accept(_token_from_digits(match, text, obfuscated=False))  # "391 035": space as separator
            elif len(groups) >= 3:
                accept(_token_from_digits(match, text, obfuscated=True))   # "5 0 0 bn": digits split apart
    # Spelled numbers go before plain digits so "5 hundred billion" style mixes
    # and "$(2.7) billion" keep their scale word.
    for match in _NUMBER_RE.finditer(text):
        accept(_token_from_digits(match, text, obfuscated=False))
    if spelled:
        for match in _SPELLED_RE.finditer(text):
            accept(_token_from_words(match, text))
    return sorted(tokens, key=lambda t: t.start)


def parse_number(text: str) -> NumToken | None:
    """Parse a string that should hold exactly one number."""
    tokens = find_tokens(text)
    return tokens[0] if len(tokens) == 1 else None

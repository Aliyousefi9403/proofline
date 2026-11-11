"""Hidden-text stripping and instruction-like text detection. Pure Python.

A report is data. Before anything reads it, this module removes content a human
reader would not see (HTML comments, hidden elements, zero-width and tag
characters) and records what was removed. It also points at sentences that read
like instructions to an AI. Detection is advisory: the checker does not depend
on it, because the LLM never produces verdicts or values.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

ZERO_WIDTH = "\u200b\u200c\u200d\u2060\ufeff\u00ad\u180e\u200e\u200f\u061c"
BIDI_CONTROLS = "\u202a\u202b\u202c\u202d\u202e\u2066\u2067\u2068\u2069"
_INVISIBLE_RE = re.compile(f"[{ZERO_WIDTH}{BIDI_CONTROLS}]")
_TAG_CHAR_RE = re.compile("[\U000e0000-\U000e007f]+")
_COMMENT_RE = re.compile(r"<!--(.*?)-->", re.DOTALL)
_HIDDEN_STYLE = r"display\s*:\s*none|visibility\s*:\s*hidden|font-size\s*:\s*0|opacity\s*:\s*0(?![.\d])"
_HIDDEN_ELEMENT_RE = re.compile(
    rf"<(?P<tag>[a-zA-Z][\w-]*)\b[^>]*(?:style\s*=\s*[\"'][^\"']*(?:{_HIDDEN_STYLE})[^\"']*[\"']|\bhidden\b)[^>]*>"
    r"(?P<body>.*?)</(?P=tag)\s*>",
    re.DOTALL | re.IGNORECASE,
)
_SCRIPT_RE = re.compile(r"<(script|style)\b[^>]*>.*?</\1\s*>", re.DOTALL | re.IGNORECASE)
_FULLWIDTH_OFFSET = 0xFEE0


@dataclass(frozen=True)
class Removal:
    kind: str       # "html_comment", "hidden_element", "zero_width", "tag_characters", "lookalike_digits"
    content: str    # what was hidden (decoded where possible)
    count: int = 1

    def to_dict(self) -> dict:
        return {"kind": self.kind, "content": self.content, "count": self.count}


@dataclass(frozen=True)
class Sanitized:
    text: str
    removals: tuple[Removal, ...] = field(default=())

    @property
    def had_hidden_content(self) -> bool:
        return bool(self.removals)


def _normalise_digits(text: str) -> tuple[str, int]:
    """Map full-width forms and non-ASCII decimal digits to ASCII."""
    out: list[str] = []
    changed = 0
    for ch in text:
        code = ord(ch)
        if 0xFF01 <= code <= 0xFF5E:
            out.append(chr(code - _FULLWIDTH_OFFSET))
            changed += 1
        elif not ch.isascii() and ch.isdigit():
            try:
                out.append(str(unicodedata.digit(ch)))
                changed += 1
            except ValueError:
                out.append(ch)
        else:
            out.append(ch)
    return "".join(out), changed


def sanitize(text: str) -> Sanitized:
    """Return the text a human would see, plus a record of what was stripped."""
    removals: list[Removal] = []

    def drop(kind: str):
        def _sub(match: re.Match) -> str:
            groups = match.groupdict()
            content = groups.get("body") or (match.group(1) if match.re is _COMMENT_RE else match.group(0))
            removals.append(Removal(kind, re.sub(r"<[^>]+>", "", content).strip()))
            return ""
        return _sub

    text = _COMMENT_RE.sub(drop("html_comment"), text)
    text = _SCRIPT_RE.sub(drop("hidden_element"), text)
    text = _HIDDEN_ELEMENT_RE.sub(drop("hidden_element"), text)

    def decode_tags(match: re.Match) -> str:
        decoded = "".join(chr(ord(c) - 0xE0000) for c in match.group(0) if 0xE0020 <= ord(c) <= 0xE007E)
        removals.append(Removal("tag_characters", decoded, len(match.group(0))))
        return ""

    text = _TAG_CHAR_RE.sub(decode_tags, text)
    invisible = _INVISIBLE_RE.findall(text)
    if invisible:
        removals.append(Removal("zero_width", f"{len(invisible)} invisible characters", len(invisible)))
        text = _INVISIBLE_RE.sub("", text)
    text, changed = _normalise_digits(text)
    if changed:
        removals.append(Removal("lookalike_digits", f"{changed} full-width or non-ASCII digits normalised", changed))
    return Sanitized(text=text, removals=tuple(removals))


_INJECTION_PATTERNS: tuple[tuple[str, str], ...] = (
    ("role_spoof", r"^\s*(?:\[|<)?\s*(?:system|assistant|developer|admin)\s*(?:\]|>)?\s*:"),
    ("addressed_to_ai", r"\b(?:note|message|instructions?|attention|memo)\s+(?:to|for)\s+(?:the\s+)?(?:ai|a\.i\.|assistant|model|llm|extractor|checker|verifier|system|agent|bot)\b"),
    ("override", r"\b(?:ignore|disregard|forget|override)\b[^.\n]{0,60}\b(?:previous|above|prior|earlier|all|your|system)\b[^.\n]{0,30}\b(?:instructions?|prompts?|rules?|guidelines?)"),
    ("suppress", r"\bdo\s+not\s+(?:extract|check|verify|flag|include|report|list|mention)\b"),
    ("suppress", r"\b(?:skip|omit|exclude|leave\s+out)\b[^.\n]{0,50}\b(?:figures?|numbers?|claims?|values?|revenue|income|sentence)\b"),
    ("pre_verified", r"\b(?:already|previously|pre-?)\s*(?:been\s+)?(?:verified|validated|checked|approved)\b"),
    ("verdict_spoof", r"\b(?:return|output|respond\s+with|mark|label|report)\b[^.\n]{0,40}\b(?:verified|accurate|correct|true|green)\b"),
    ("rewrite", r"\b(?:when|while|during)\s+(?:extracting|extraction|parsing|reading|checking)\b"),
    ("rewrite", r"\b(?:record|treat|read|interpret|extract|log|use)\s+(?:the\s+)?[\w\s]{1,30}\s+as\s+\$?\d"),
)
_INJECTION_RES = tuple((name, re.compile(pattern, re.IGNORECASE)) for name, pattern in _INJECTION_PATTERNS)
_SENTENCE_RE = re.compile(r"[^.!?\n]+[.!?]?")


def find_injections(text: str) -> list[dict]:
    """Sentences that read like instructions to an AI. Advisory only."""
    hits: list[dict] = []
    for sentence in _SENTENCE_RE.finditer(text):
        body = sentence.group(0)
        for name, pattern in _INJECTION_RES:
            if pattern.search(body):
                stripped = body.strip()
                start = sentence.start() + len(body) - len(body.lstrip())
                hits.append({"start": start, "end": start + len(stripped), "text": stripped, "pattern": name})
                break
    return hits

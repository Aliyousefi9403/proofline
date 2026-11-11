"""Fetch the financial statements of each company's 10-K as plain text.

The leaderboard's "with the 10-K supplied" mode gives a model these pages. For
each fiscal year the script finds the 10-K that first reported it, reads the
filing's FilingSummary.xml to locate the primary statements, and saves them to
data/filings/{TICKER}_FY{year}.txt. Fetched once; everything else runs offline.

Usage:  uv run python scripts/fetch_filings.py [first_year last_year]
"""
from __future__ import annotations

import re
import sys
import time
import xml.etree.ElementTree as ET
from html.parser import HTMLParser
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend import config  # noqa: E402
from backend.edgar import FactStore  # noqa: E402

STATEMENT_PATTERNS = (
    r"statements? of (?:operations|income|earnings)(?! \(parenthetical\))",
    r"income statements?",
    r"balance sheets?(?! \(parenthetical\))",
    r"statements? of financial position",
    r"statements? of cash flows?",
)
SKIP_PATTERN = r"parenthetical|comprehensive|details|tables|policies"
MAX_STATEMENTS = 4


class _TableText(HTMLParser):
    """Turn an EDGAR statement page into tab-separated rows."""

    def __init__(self) -> None:
        super().__init__()
        self.rows: list[list[str]] = []
        self._row: list[str] | None = None
        self._cell: list[str] | None = None
        self._skip = 0
        self._reference_depth = 0  # inside an XBRL definition table (not statement content)

    def handle_starttag(self, tag, attrs):
        if tag == "table":
            classes = dict(attrs).get("class") or ""
            if self._reference_depth or "authRefData" in classes:
                self._reference_depth += 1
                return
        if self._reference_depth:
            return
        if tag in ("script", "style"):
            self._skip += 1
        elif tag == "tr":
            self._row = []
        elif tag in ("td", "th") and self._row is not None:
            self._cell = []

    def handle_endtag(self, tag):
        if self._reference_depth:
            if tag == "table":
                self._reference_depth -= 1
            return
        if tag in ("script", "style"):
            self._skip = max(0, self._skip - 1)
        elif tag in ("td", "th") and self._row is not None and self._cell is not None:
            self._row.append(re.sub(r"\s+", " ", "".join(self._cell)).strip())
            self._cell = None
        elif tag == "tr" and self._row is not None:
            if any(self._row):
                self.rows.append(self._row)
            self._row = None

    def handle_data(self, data):
        if self._cell is not None and not self._skip and not self._reference_depth:
            self._cell.append(data)


def page_to_text(html: str) -> str:
    parser = _TableText()
    parser.feed(html)
    lines = []
    for row in parser.rows:
        cells = [c for c in row if c and not re.fullmatch(r"\[\d+\]", c)]
        if cells and cells != ["X"]:
            lines.append(" | ".join(cells))
    return "\n".join(lines)


def pick_statements(summary_xml: str) -> list[tuple[str, str]]:
    """(title, file) for the primary financial statements in a filing."""
    root = ET.fromstring(summary_xml)
    picked: list[tuple[str, str]] = []
    for report in root.iter("Report"):
        name = (report.findtext("ShortName") or "").strip()
        file = report.findtext("HtmlFileName")
        if not file or re.search(SKIP_PATTERN, name, re.IGNORECASE):
            continue
        if any(re.search(pattern, name, re.IGNORECASE) for pattern in STATEMENT_PATTERNS):
            picked.append((name, file))
        if len(picked) >= MAX_STATEMENTS:
            break
    return picked


def original_filing(store: FactStore, ticker: str, year: int) -> tuple[str, str] | None:
    """Accession and filing date of the 10-K that first reported this fiscal year."""
    fact = store.get_fact(ticker, "revenue", year)
    if fact is None or not fact.reported:
        return None
    first = min(fact.reported, key=lambda r: r.filed)
    return first.accn, first.filed


def main(argv: list[str]) -> int:
    import httpx

    config.load_dotenv()
    first_year = int(argv[0]) if argv else 2023
    last_year = int(argv[1]) if len(argv) > 1 else 2025
    store = FactStore()
    config.FILINGS_DIR.mkdir(parents=True, exist_ok=True)
    headers = {"User-Agent": config.SEC_USER_AGENT, "Accept-Encoding": "gzip, deflate"}
    with httpx.Client(headers=headers, timeout=60.0, follow_redirects=True) as client:
        def get(url: str) -> str:
            time.sleep(config.SEC_MIN_INTERVAL_SECONDS)
            response = client.get(url)
            response.raise_for_status()
            return response.text

        for ticker in store.tickers():
            cik = store.company(ticker)["cik"]
            for year in range(first_year, last_year + 1):
                out = config.FILINGS_DIR / f"{ticker}_FY{year}.txt"
                if out.exists():
                    continue
                found = original_filing(store, ticker, year)
                if found is None:
                    print(f"{ticker} FY{year}: no 10-K on record")
                    continue
                accn, filed = found
                base = f"https://www.sec.gov/Archives/edgar/data/{cik}/{accn.replace('-', '')}"
                try:
                    statements = pick_statements(get(f"{base}/FilingSummary.xml"))
                    parts = [
                        f"{store.company_name(ticker)} ({ticker}) Form 10-K for fiscal year {year}, "
                        f"filed {filed}, accession {accn}. Source: {base}/"
                    ]
                    for title, file in statements:
                        parts.append(f"\n## {title}\n{page_to_text(get(f'{base}/{file}'))}")
                except (httpx.HTTPError, ET.ParseError) as exc:
                    print(f"{ticker} FY{year}: failed ({type(exc).__name__}: {exc})")
                    continue
                out.write_text("\n".join(parts) + "\n", encoding="utf-8")
                print(f"{ticker} FY{year}: {len(statements)} statements, {out.stat().st_size // 1024} KB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

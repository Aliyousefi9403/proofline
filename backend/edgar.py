"""EDGAR data fetcher and local fact store.

Two halves:

* ``fetch_company`` / ``build_cache`` talk to the SEC once and write a slim JSON
  file per ticker under ``data/``.
* ``FactStore`` answers ``get_fact(ticker, metric, fiscal_year)`` from those
  files only. It never touches the network, so the checker runs offline.

The slim cache keeps the same shape as the SEC ``companyfacts`` payload
(``facts[taxonomy][tag]["units"][unit] -> rows``), restricted to the tags in
``METRICS`` and to annual-report forms.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Iterable

from . import config

TICKER_MAP_URL = "https://www.sec.gov/files/company_tickers.json"
FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"
ANNUAL_FORMS = ("10-K", "10-K/A")
ANNUAL_MIN_DAYS = 350
ANNUAL_MAX_DAYS = 380
# A 52/53-week year can end in the first days of January; that still belongs
# to the previous fiscal year.
EARLY_JANUARY_CUTOFF_DAY = 10
COVER_PAGE_WINDOW_DAYS = 120


@dataclass(frozen=True)
class MetricSpec:
    """How a metric is found in XBRL.

    ``tags`` are tried in order, per fiscal period: the first tag that has a
    value for the period wins. ``derived`` is an optional fallback formula
    ``(metric_a, "-", metric_b)`` used when no tag has data.
    """

    key: str
    label: str
    unit: str  # XBRL unit key: "USD", "USD/shares", "shares"
    instant: bool
    tags: tuple[tuple[str, str], ...]  # (taxonomy, tag)
    derived: tuple[str, str, str] | None = None
    public: bool = True


def _gaap(*names: str) -> tuple[tuple[str, str], ...]:
    return tuple(("us-gaap", n) for n in names)


METRICS: dict[str, MetricSpec] = {
    m.key: m
    for m in (
        # "Revenues" is tried first: where a company reports both, it is the
        # total (Tesla's contract-revenue tag leaves out leasing).
        MetricSpec("revenue", "Revenue", "USD", False, _gaap(
            "Revenues",
            "RevenueFromContractWithCustomerExcludingAssessedTax",
            "SalesRevenueNet",
            "RevenueFromContractWithCustomerIncludingAssessedTax")),
        MetricSpec("net_income", "Net income", "USD", False, _gaap("NetIncomeLoss", "ProfitLoss")),
        MetricSpec("net_income_total", "Net income including noncontrolling interests", "USD", False,
                   _gaap("ProfitLoss"), public=False),
        MetricSpec("cost_of_revenue", "Cost of revenue", "USD", False, _gaap(
            "CostOfRevenue", "CostOfGoodsAndServicesSold", "CostOfGoodsSold")),
        MetricSpec("gross_profit", "Gross profit", "USD", False, _gaap("GrossProfit"),
                   derived=("revenue", "-", "cost_of_revenue")),
        MetricSpec("operating_income", "Operating income", "USD", False, _gaap("OperatingIncomeLoss")),
        MetricSpec("rd_expense", "R&D expense", "USD", False, _gaap("ResearchAndDevelopmentExpense")),
        MetricSpec("operating_cash_flow", "Operating cash flow", "USD", False, _gaap(
            "NetCashProvidedByUsedInOperatingActivities")),
        MetricSpec("eps_diluted", "Diluted EPS", "USD/shares", False, _gaap("EarningsPerShareDiluted")),
        MetricSpec("eps_basic", "Basic EPS", "USD/shares", False, _gaap("EarningsPerShareBasic")),
        MetricSpec("total_assets", "Total assets", "USD", True, _gaap("Assets")),
        MetricSpec("liabilities_and_equity", "Total liabilities and equity", "USD", True, _gaap(
            "LiabilitiesAndStockholdersEquity"), public=False),
        MetricSpec("stockholders_equity", "Stockholders' equity", "USD", True, _gaap(
            "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
            "StockholdersEquity")),
        MetricSpec("total_liabilities", "Total liabilities", "USD", True, _gaap("Liabilities"),
                   derived=("liabilities_and_equity", "-", "stockholders_equity")),
        MetricSpec("cash", "Cash and cash equivalents", "USD", True, _gaap(
            "CashAndCashEquivalentsAtCarryingValue")),
        MetricSpec("shares_outstanding", "Shares outstanding", "shares", True, (
            ("us-gaap", "CommonStockSharesOutstanding"),
            ("dei", "EntityCommonStockSharesOutstanding"))),
    )
}

PUBLIC_METRICS: tuple[str, ...] = tuple(k for k, m in METRICS.items() if m.public)

# Some lines have two legitimate readings. A claim that matches either is accepted, and the
# verdict says which one it matched.
METRIC_VARIANTS: dict[str, tuple[str, ...]] = {"net_income": ("net_income_total",)}

# Friendly names used to resolve a company mention to a ticker.
COMPANY_ALIASES: dict[str, tuple[str, ...]] = {
    "AAPL": ("apple",),
    "MSFT": ("microsoft",),
    "NVDA": ("nvidia",),
    "TSLA": ("tesla",),
    "AMZN": ("amazon", "amazon.com"),
}


@dataclass(frozen=True)
class Reported:
    """One as-filed value for a period (a period can be restated later)."""

    value: float
    accn: str
    form: str
    filed: str
    tag: str


@dataclass(frozen=True)
class Fact:
    ticker: str
    metric: str
    fiscal_year: int
    value: float
    unit: str
    start: str | None
    end: str
    accn: str
    form: str
    filed: str
    tag: str
    url: str
    derived: bool = False
    formula: str | None = None
    components: tuple["Fact", ...] = ()
    reported: tuple[Reported, ...] = field(default=())  # every filing that states this period, newest first

    @property
    def other_reported(self) -> tuple[Reported, ...]:
        """As-filed values that differ from the latest one (restatements, split adjustments)."""
        seen = {self.value}
        out: list[Reported] = []
        for row in self.reported:
            if row.value not in seen:
                seen.add(row.value)
                out.append(row)
        return tuple(out)

    def to_dict(self) -> dict:
        return {
            "ticker": self.ticker,
            "metric": self.metric,
            "fiscal_year": self.fiscal_year,
            "value": self.value,
            "unit": self.unit,
            "start": self.start,
            "end": self.end,
            "accn": self.accn,
            "form": self.form,
            "filed": self.filed,
            "tag": self.tag,
            "url": self.url,
            "derived": self.derived,
            "formula": self.formula,
            "components": [c.to_dict() for c in self.components],
            "reported": [
                {"value": r.value, "accn": r.accn, "form": r.form, "filed": r.filed,
                 "restated": r.value != self.value}
                for r in self.reported
            ],
        }


def filing_url(cik: int, accn: str) -> str:
    """Link to the filing index page on sec.gov, built from the accession number."""
    return f"https://www.sec.gov/Archives/edgar/data/{cik}/{accn.replace('-', '')}/{accn}-index.htm"


def _days(start: str, end: str) -> int:
    return (date.fromisoformat(end) - date.fromisoformat(start)).days


def fiscal_year_of(end: str) -> int:
    """Fiscal year label for a period end date: the calendar year it ends in."""
    d = date.fromisoformat(end)
    if d.month == 1 and d.day <= EARLY_JANUARY_CUTOFF_DAY:
        return d.year - 1
    return d.year


def _is_annual(row: dict) -> bool:
    if row.get("form") not in ANNUAL_FORMS:
        return False
    if "start" not in row:
        return True
    return ANNUAL_MIN_DAYS <= _days(row["start"], row["end"]) <= ANNUAL_MAX_DAYS


# --------------------------------------------------------------------------
# Fetching (network). Only this section uses httpx.
# --------------------------------------------------------------------------

def slim_company_facts(raw: dict, ticker: str) -> dict:
    """Reduce a raw ``companyfacts`` payload to the tags and forms we check."""
    wanted: dict[str, set[str]] = {}
    for spec in METRICS.values():
        for taxonomy, tag in spec.tags:
            wanted.setdefault(taxonomy, set()).add(tag)
    facts: dict[str, dict] = {}
    for taxonomy, tags in wanted.items():
        for tag in sorted(tags):
            node = raw.get("facts", {}).get(taxonomy, {}).get(tag)
            if not node:
                continue
            units = {
                unit: [r for r in rows if r.get("form") in ANNUAL_FORMS]
                for unit, rows in node.get("units", {}).items()
            }
            units = {u: rows for u, rows in units.items() if rows}
            if units:
                facts.setdefault(taxonomy, {})[tag] = {"label": node.get("label"), "units": units}
    return {
        "ticker": ticker.upper(),
        "cik": int(raw["cik"]),
        "entityName": raw.get("entityName", ticker.upper()),
        "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": FACTS_URL.format(cik=int(raw["cik"])),
        "facts": facts,
    }


def fetch_ticker_map(client) -> dict[str, dict]:
    """Ticker -> {"cik": int, "title": str} from the SEC ticker file."""
    resp = client.get(TICKER_MAP_URL)
    resp.raise_for_status()
    return {
        row["ticker"].upper(): {"cik": int(row["cik_str"]), "title": row["title"]}
        for row in resp.json().values()
    }


def fetch_company(ticker: str, client, ticker_map: dict[str, dict]) -> dict:
    ticker = ticker.upper()
    if ticker not in ticker_map:
        raise KeyError(f"Unknown ticker: {ticker}")
    resp = client.get(FACTS_URL.format(cik=ticker_map[ticker]["cik"]))
    resp.raise_for_status()
    return slim_company_facts(resp.json(), ticker)


def build_cache(tickers: Iterable[str] = config.DEFAULT_TICKERS, data_dir: Path | None = None) -> list[Path]:
    """Fetch each ticker once and write ``data/{ticker}.json``."""
    import httpx  # local import keeps the offline store free of network code

    out_dir = data_dir or config.DATA_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    headers = {"User-Agent": config.SEC_USER_AGENT, "Accept-Encoding": "gzip, deflate"}
    with httpx.Client(headers=headers, timeout=60.0) as client:
        ticker_map = fetch_ticker_map(client)
        for ticker in tickers:
            time.sleep(config.SEC_MIN_INTERVAL_SECONDS)
            slim = fetch_company(ticker, client, ticker_map)
            path = out_dir / f"{ticker.upper()}.json"
            path.write_text(json.dumps(slim, separators=(",", ":")), encoding="utf-8")
            written.append(path)
    return written


# --------------------------------------------------------------------------
# Offline fact store
# --------------------------------------------------------------------------

class FactStore:
    """Read-only view over the cached company files."""

    def __init__(self, data_dir: Path | None = None):
        self.data_dir = Path(data_dir or config.DATA_DIR)
        self._companies: dict[str, dict] = {}
        self._calendars: dict[str, dict[int, tuple[str, str]]] = {}
        self._fact_cache: dict[tuple[str, str, int], Fact | None] = {}

    # ---- companies -------------------------------------------------------

    def tickers(self) -> list[str]:
        return sorted(
            p.stem for p in self.data_dir.glob("*.json") if p.stem.isupper()
        )

    def has(self, ticker: str) -> bool:
        return (self.data_dir / f"{ticker.upper()}.json").exists()

    def company(self, ticker: str) -> dict | None:
        ticker = ticker.upper()
        if ticker not in self._companies:
            path = self.data_dir / f"{ticker}.json"
            if not path.exists():
                return None
            self._companies[ticker] = json.loads(path.read_text(encoding="utf-8"))
        return self._companies[ticker]

    def company_name(self, ticker: str) -> str:
        data = self.company(ticker)
        return data["entityName"] if data else ticker.upper()

    def resolve(self, mention: str) -> str | None:
        """Map a ticker or company name ('Apple', 'apple inc.') to a cached ticker."""
        if not mention:
            return None
        cleaned = mention.strip().strip(".,").lower()
        for ticker in self.tickers():
            if cleaned == ticker.lower():
                return ticker
            if cleaned in COMPANY_ALIASES.get(ticker, ()):
                return ticker
            name = self.company_name(ticker).lower()
            first_word = name.replace(",", " ").replace(".", " ").split()[0]
            if cleaned == name or cleaned.split()[0] == first_word:
                return ticker
        return None

    def snapshot_id(self) -> str:
        """Hash of the cached data, so a certificate records which data it used."""
        import hashlib

        digest = hashlib.sha256()
        for ticker in self.tickers():
            digest.update(ticker.encode())
            digest.update((self.data_dir / f"{ticker}.json").read_bytes())
        return digest.hexdigest()

    # ---- periods ---------------------------------------------------------

    def _rows(self, ticker: str, taxonomy: str, tag: str, unit: str) -> list[dict]:
        data = self.company(ticker)
        if not data:
            return []
        node = data["facts"].get(taxonomy, {}).get(tag)
        if not node:
            return []
        return [r for r in node["units"].get(unit, []) if _is_annual(r)]

    def calendar(self, ticker: str) -> dict[int, tuple[str, str]]:
        """Fiscal year -> (start, end), read from annual revenue and net income periods."""
        ticker = ticker.upper()
        if ticker in self._calendars:
            return self._calendars[ticker]
        latest_filed: dict[int, tuple[str, str, str]] = {}
        for metric in ("revenue", "net_income"):
            spec = METRICS[metric]
            for taxonomy, tag in spec.tags:
                for row in self._rows(ticker, taxonomy, tag, spec.unit):
                    if "start" not in row:
                        continue
                    fy = fiscal_year_of(row["end"])
                    current = latest_filed.get(fy)
                    if current is None or row["filed"] > current[2]:
                        latest_filed[fy] = (row["start"], row["end"], row["filed"])
        calendar = {fy: (s, e) for fy, (s, e, _) in sorted(latest_filed.items())}
        self._calendars[ticker] = calendar
        return calendar

    def fiscal_years(self, ticker: str) -> list[int]:
        return sorted(self.calendar(ticker))

    # ---- facts -----------------------------------------------------------

    def get_fact(self, ticker: str, metric: str, fiscal_year: int) -> Fact | None:
        ticker = ticker.upper()
        key = (ticker, metric, fiscal_year)
        if key not in self._fact_cache:
            self._fact_cache[key] = self._build_fact(ticker, metric, fiscal_year)
        return self._fact_cache[key]

    def _build_fact(self, ticker: str, metric: str, fiscal_year: int) -> Fact | None:
        spec = METRICS.get(metric)
        data = self.company(ticker)
        if spec is None or data is None:
            return None
        period = self.calendar(ticker).get(fiscal_year)
        if period is None:
            return None
        start, end = period
        for taxonomy, tag in spec.tags:
            rows = self._rows(ticker, taxonomy, tag, spec.unit)
            matches = [r for r in rows if self._row_matches(r, spec, taxonomy, start, end)]
            if matches:
                return self._fact_from_rows(data, spec, fiscal_year, tag, matches)
        if spec.derived:
            return self._derive(ticker, spec, fiscal_year)
        return None

    @staticmethod
    def _row_matches(row: dict, spec: MetricSpec, taxonomy: str, start: str, end: str) -> bool:
        if not spec.instant:
            return row.get("start") == start and row["end"] == end
        if "start" in row:
            return False
        if taxonomy == "dei":
            # Cover-page share counts are dated a few weeks after the year end.
            return 0 <= _days(end, row["end"]) <= COVER_PAGE_WINDOW_DAYS
        return row["end"] == end

    @staticmethod
    def _fact_from_rows(data: dict, spec: MetricSpec, fiscal_year: int, tag: str, rows: list[dict]) -> Fact:
        ordered = sorted(rows, key=lambda r: (r["filed"], r["accn"]), reverse=True)
        primary = ordered[0]
        seen_accn: set[str] = set()
        reported: list[Reported] = []
        for row in ordered:
            if row["accn"] in seen_accn:
                continue
            seen_accn.add(row["accn"])
            reported.append(Reported(float(row["val"]), row["accn"], row["form"], row["filed"], tag))
        return Fact(
            ticker=data["ticker"],
            metric=spec.key,
            fiscal_year=fiscal_year,
            value=float(primary["val"]),
            unit=spec.unit,
            start=primary.get("start"),
            end=primary["end"],
            accn=primary["accn"],
            form=primary["form"],
            filed=primary["filed"],
            tag=tag,
            url=filing_url(data["cik"], primary["accn"]),
            reported=tuple(reported),
        )

    def _derive(self, ticker: str, spec: MetricSpec, fiscal_year: int) -> Fact | None:
        left_key, op, right_key = spec.derived  # type: ignore[misc]
        left = self.get_fact(ticker, left_key, fiscal_year)
        right = self.get_fact(ticker, right_key, fiscal_year)
        if left is None or right is None or op != "-":
            return None
        newest = max((left, right), key=lambda f: f.filed)
        return Fact(
            ticker=ticker,
            metric=spec.key,
            fiscal_year=fiscal_year,
            value=left.value - right.value,
            unit=spec.unit,
            start=left.start,
            end=left.end,
            accn=newest.accn,
            form=newest.form,
            filed=newest.filed,
            tag=f"{left.tag} - {right.tag}",
            url=newest.url,
            derived=True,
            formula=f"{METRICS[left_key].label} - {METRICS[right_key].label}",
            components=(left, right),
        )

    def common_basis(self, a: Fact, b: Fact) -> tuple[float, float, Reported] | None:
        """Values of two facts as stated in one filing (the newest that has both).

        Year-on-year comparisons should use a single filing: later filings restate
        earlier periods, and per-share figures change basis after a stock split.
        """
        by_accn = {r.accn: r for r in a.reported}
        for row in b.reported:
            if row.accn in by_accn:
                return by_accn[row.accn].value, row.value, row
        return None

    def years_with(self, ticker: str, metric: str) -> list[int]:
        return [fy for fy in self.fiscal_years(ticker) if self.get_fact(ticker, metric, fy) is not None]


@lru_cache(maxsize=1)
def default_store() -> FactStore:
    return FactStore()


def get_fact(ticker: str, metric: str, fiscal_year: int) -> Fact | None:
    """``get_fact("AAPL", "revenue", 2024)`` -> the filed figure, or None."""
    return default_store().get_fact(ticker, metric, fiscal_year)

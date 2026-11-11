"""The fact store, checked against figures read from the 10-K filings."""
import json

import pytest

from backend import edgar
from backend.edgar import FactStore, fiscal_year_of, get_fact

KNOWN = [
    ("AAPL", "revenue", 2024, 391_035_000_000),
    ("AAPL", "revenue", 2023, 383_285_000_000),
    ("AAPL", "net_income", 2024, 93_736_000_000),
    ("AAPL", "gross_profit", 2024, 180_683_000_000),
    ("AAPL", "operating_income", 2024, 123_216_000_000),
    ("AAPL", "total_assets", 2024, 364_980_000_000),
    ("AAPL", "total_liabilities", 2024, 308_030_000_000),
    ("AAPL", "cash", 2024, 29_943_000_000),
    ("AAPL", "rd_expense", 2024, 31_370_000_000),
    ("AAPL", "eps_diluted", 2024, 6.08),
    ("MSFT", "revenue", 2024, 245_122_000_000),
    ("MSFT", "net_income", 2024, 88_136_000_000),
    ("NVDA", "revenue", 2025, 130_497_000_000),
    ("NVDA", "revenue", 2024, 60_922_000_000),
    ("TSLA", "revenue", 2023, 96_773_000_000),
    ("TSLA", "net_income", 2023, 14_997_000_000),
    ("AMZN", "revenue", 2023, 574_785_000_000),
    ("AMZN", "net_income", 2022, -2_722_000_000),
]


@pytest.mark.parametrize("ticker,metric,year,expected", KNOWN)
def test_get_fact_returns_filed_figure(store, ticker, metric, year, expected):
    fact = store.get_fact(ticker, metric, year)
    assert fact is not None
    assert fact.value == pytest.approx(expected)
    assert fact.form in ("10-K", "10-K/A")


def test_module_level_get_fact_matches_the_brief_example():
    fact = get_fact("AAPL", "revenue", 2024)
    assert fact is not None and fact.value == 391_035_000_000


def test_fiscal_year_follows_period_end_not_filing_fy(store):
    # Apple's year ends in late September; Microsoft's in June; NVIDIA's in late January.
    assert store.calendar("AAPL")[2024] == ("2023-10-01", "2024-09-28")
    assert store.calendar("MSFT")[2024] == ("2023-07-01", "2024-06-30")
    assert store.calendar("NVDA")[2025] == ("2024-01-29", "2025-01-26")


def test_early_january_year_end_belongs_to_previous_fiscal_year():
    assert fiscal_year_of("2023-01-01") == 2022
    assert fiscal_year_of("2025-01-26") == 2025
    assert fiscal_year_of("2024-12-31") == 2024


def test_annual_filter_rejects_quarters_and_non_10k():
    assert edgar._is_annual({"form": "10-K", "start": "2023-10-01", "end": "2024-09-28"})
    assert not edgar._is_annual({"form": "10-K", "start": "2024-06-30", "end": "2024-09-28"})
    assert not edgar._is_annual({"form": "10-Q", "start": "2023-10-01", "end": "2024-09-28"})
    assert edgar._is_annual({"form": "10-K", "end": "2024-09-28"})


def test_revenue_tag_is_chosen_per_period(store):
    # NVIDIA switched tags over time; both eras must resolve.
    assert store.get_fact("NVDA", "revenue", 2012) is not None
    assert store.get_fact("NVDA", "revenue", 2025).tag == "Revenues"
    # Tesla's contract-revenue tag leaves out leasing; the total must win.
    assert store.get_fact("TSLA", "revenue", 2023).tag == "Revenues"
    assert store.get_fact("AAPL", "revenue", 2012).tag == "SalesRevenueNet"


def test_derived_metric_shows_its_working(store):
    gross = store.get_fact("AMZN", "gross_profit", 2023)
    assert gross.derived and gross.formula == "Revenue - Cost of revenue"
    assert gross.value == pytest.approx(574_785_000_000 - 304_739_000_000)
    assert [c.metric for c in gross.components] == ["revenue", "cost_of_revenue"]
    liabilities = store.get_fact("AMZN", "total_liabilities", 2023)
    assert liabilities.derived
    assert liabilities.value == pytest.approx(527_854_000_000 - 201_875_000_000)


def test_missing_data_returns_none(store):
    assert store.get_fact("AMZN", "rd_expense", 2023) is None   # Amazon does not tag R&D
    assert store.get_fact("AAPL", "revenue", 1999) is None
    assert store.get_fact("AAPL", "not_a_metric", 2024) is None
    assert store.get_fact("ZZZZ", "revenue", 2024) is None


def test_latest_filing_wins_and_restatements_are_kept(store):
    eps = store.get_fact("NVDA", "eps_diluted", 2024)
    assert eps.value == pytest.approx(1.19)           # restated after the 10-for-1 split
    assert 11.93 in [r.value for r in eps.other_reported]
    filed = [r.filed for r in eps.reported]
    assert filed == sorted(filed, reverse=True)


def test_common_basis_uses_one_filing(store):
    a = store.get_fact("NVDA", "eps_diluted", 2022)
    b = store.get_fact("NVDA", "eps_diluted", 2023)
    va, vb, row = store.common_basis(a, b)
    assert (va, vb) == (pytest.approx(3.85), pytest.approx(1.74))
    far = store.get_fact("NVDA", "eps_diluted", 2025)
    assert store.common_basis(a, far) is None


def test_filing_url_is_built_from_accession(store):
    fact = store.get_fact("AAPL", "revenue", 2024)
    assert fact.url == edgar.filing_url(320193, fact.accn)
    assert fact.accn.replace("-", "") in fact.url and fact.url.endswith("-index.htm")


def test_resolve_company_names(store):
    assert store.resolve("Apple") == "AAPL"
    assert store.resolve("apple inc.") == "AAPL"
    assert store.resolve("msft") == "MSFT"
    assert store.resolve("Amazon.com") == "AMZN"
    assert store.resolve("Initech") is None
    assert store.resolve("") is None


def test_slim_company_facts_keeps_only_needed_annual_rows():
    raw = {
        "cik": 1, "entityName": "Demo Co",
        "facts": {"us-gaap": {
            "Revenues": {"label": "Revenues", "units": {"USD": [
                {"start": "2023-01-01", "end": "2023-12-31", "val": 10, "accn": "a", "fy": 2023, "fp": "FY", "form": "10-K", "filed": "2024-02-01"},
                {"start": "2023-01-01", "end": "2023-03-31", "val": 2, "accn": "b", "fy": 2023, "fp": "Q1", "form": "10-Q", "filed": "2023-05-01"},
            ]}},
            "Goodwill": {"label": "Goodwill", "units": {"USD": [
                {"end": "2023-12-31", "val": 5, "accn": "a", "fy": 2023, "fp": "FY", "form": "10-K", "filed": "2024-02-01"}]}},
        }},
    }
    slim = edgar.slim_company_facts(raw, "demo")
    assert slim["ticker"] == "DEMO"
    assert list(slim["facts"]["us-gaap"]) == ["Revenues"]
    assert len(slim["facts"]["us-gaap"]["Revenues"]["units"]["USD"]) == 1


def test_store_reads_a_custom_directory(tmp_path):
    raw = {
        "ticker": "DEMO", "cik": 1, "entityName": "Demo Co", "facts": {"us-gaap": {
            "Revenues": {"units": {"USD": [
                {"start": "2023-01-01", "end": "2023-12-31", "val": 10, "accn": "0000000001-24-000001", "form": "10-K", "filed": "2024-02-01"},
                {"start": "2023-01-01", "end": "2023-12-31", "val": 12, "accn": "0000000001-25-000001", "form": "10-K", "filed": "2025-02-01"},
            ]}}}},
    }
    (tmp_path / "DEMO.json").write_text(json.dumps(raw))
    demo = FactStore(tmp_path)
    assert demo.tickers() == ["DEMO"]
    fact = demo.get_fact("DEMO", "revenue", 2023)
    assert fact.value == 12 and [r.value for r in fact.other_reported] == [10]
    assert len(demo.snapshot_id()) == 64
    assert demo.years_with("DEMO", "revenue") == [2023]


def test_fetch_uses_the_sec_endpoints():
    calls = []

    class FakeResponse:
        def __init__(self, payload):
            self._payload = payload

        def raise_for_status(self):
            return None

        def json(self):
            return self._payload

    class FakeClient:
        def get(self, url):
            calls.append(url)
            if url == edgar.TICKER_MAP_URL:
                return FakeResponse({"0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."}})
            return FakeResponse({"cik": 320193, "entityName": "Apple Inc.", "facts": {}})

    client = FakeClient()
    ticker_map = edgar.fetch_ticker_map(client)
    slim = edgar.fetch_company("aapl", client, ticker_map)
    assert calls[1] == "https://data.sec.gov/api/xbrl/companyfacts/CIK0000320193.json"
    assert slim["ticker"] == "AAPL"
    with pytest.raises(KeyError):
        edgar.fetch_company("NOPE", client, ticker_map)

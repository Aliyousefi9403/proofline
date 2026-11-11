"""Checker tests. Hand-written claims, real filing data, no LLM anywhere."""
import ast
from pathlib import Path

import pytest

from backend import check
from backend.check import DEFAULT_POLICY, Policy, check_claim, matches
from backend.models import Status


def run(store, claim, quantum=None, **kw):
    return check_claim(claim, store, quantum=quantum, **kw)


# ---- tolerance -------------------------------------------------------------

@pytest.mark.parametrize("claimed,actual,quantum,ok", [
    (391e9, 391.035e9, 1e9, True),      # "$391bn" accepts 390.5 to 391.5bn
    (391e9, 391.49e9, 1e9, True),
    (391e9, 391.6e9, 1e9, True),        # truncation of a 3-digit figure
    (391e9, 392.1e9, 1e9, False),
    (391e9, 390.4e9, 1e9, False),
    (391.0e9, 391.035e9, 1e8, True),
    (391.1e9, 391.035e9, 1e8, False),
    (94.9e9, 94.97e9, 1e8, True),       # truncated, fine-grained
    (2e12, 2.4e12, 1e12, True),         # rounds to 2 trillion
    (2e12, 2.9e12, 1e12, False),        # coarse figure: truncation not accepted
    (6.08, 6.08, 0.01, True),
    (6.08, 6.11, 0.01, False),
    (100.0, 100.4, None, True),         # no stated precision: 0.5%
    (100.0, 100.6, None, False),
    (2.7e9, -2.722e9, 1e8, True),       # loss stated as a magnitude
    (-2.7e9, 2.722e9, 1e8, False),
])
def test_absolute_tolerance(claimed, actual, quantum, ok):
    assert matches(claimed, actual, quantum, "absolute") is ok


@pytest.mark.parametrize("claimed,actual,ok", [(2.0, 2.02, True), (2.5, 2.02, True), (2.6, 2.02, False), (-2.8, -2.8, True)])
def test_percentage_tolerance_is_half_a_point(claimed, actual, ok):
    assert matches(claimed, actual, 0.1, "growth_pct") is ok


def test_policy_is_configurable_and_serialisable():
    strict = Policy(pct_points=0.1)
    assert not matches(2.5, 2.02, 0.1, "growth_pct", strict)
    assert DEFAULT_POLICY.to_dict() == {"pct_points": 0.5, "relative": 0.005, "truncation_max_step": 0.02}


# ---- absolute claims ---------------------------------------------------------

def test_verified_absolute(store, make_claim):
    verdict = run(store, make_claim(), 1e8)
    assert verdict.status is Status.VERIFIED
    assert verdict.expected == 391_035_000_000
    assert verdict.filing_urls and "sec.gov/Archives/edgar/data/320193" in verdict.filing_urls[0]
    assert "$391,035,000,000" in verdict.calculation and "10-K" in verdict.calculation
    assert verdict.sources[0]["accn"]


def test_wrong_value_carries_the_correct_figure(store, make_claim):
    verdict = run(store, make_claim(value=500e9), 1e9)
    assert verdict.status is Status.WRONG_VALUE
    assert verdict.expected == 391_035_000_000
    assert "$391.035bn" in verdict.reason and "27.9% too high" in verdict.reason


def test_wrong_period_names_the_year_it_matches(store, make_claim):
    verdict = run(store, make_claim(value=383.3e9), 1e8)
    assert verdict.status is Status.WRONG_PERIOD
    assert verdict.matched_year == 2023
    assert "FY2023" in verdict.reason


@pytest.mark.parametrize("value,quantum,factor", [(391.0e6, 1e5, 1e-3), (391_035.0, 1.0, 1e-6), (391.0e12, 1e11, 1e3)])
def test_scale_error(store, make_claim, value, quantum, factor):
    verdict = run(store, make_claim(value=value), quantum)
    assert verdict.status is Status.SCALE_ERROR
    assert verdict.scale_factor == pytest.approx(1 / factor) or verdict.scale_factor == pytest.approx(factor)


def test_unsupported_when_no_source(store, make_claim):
    assert run(store, make_claim(company="AMZN", metric="rd_expense", fiscal_year=2023, value=85e9), 1e9).status is Status.UNSUPPORTED
    assert run(store, make_claim(company="Initech")).status is Status.UNSUPPORTED
    assert run(store, make_claim(metric="other")).status is Status.UNSUPPORTED
    future = run(store, make_claim(fiscal_year=2031), 1e8)
    assert future.status is Status.UNSUPPORTED and future.hint == "The figure matches FY2024."


def test_missing_year_is_amber_with_a_hint(store, make_claim):
    verdict = run(store, make_claim(fiscal_year=None), 1e8)
    assert verdict.status is Status.UNSUPPORTED
    assert verdict.hint == "The figure matches FY2024."


def test_loss_stated_as_magnitude_is_verified(store, make_claim):
    verdict = run(store, make_claim(company="AMZN", metric="net_income", fiscal_year=2022, value=2.7e9), 1e8)
    assert verdict.status is Status.VERIFIED


def test_hint_when_value_belongs_to_another_metric(store, make_claim):
    verdict = run(store, make_claim(metric="net_income", value=123.2e9), 1e8)
    assert verdict.status is Status.WRONG_VALUE
    assert "Operating income" in verdict.hint


def test_as_originally_filed_value_is_verified(store, make_claim):
    verdict = run(store, make_claim(company="NVDA", metric="eps_diluted", fiscal_year=2024, value=11.93, unit="USD_per_share"), 0.01)
    assert verdict.status is Status.VERIFIED
    assert "as_originally_filed" in verdict.flags


def test_split_adjusted_figure_is_amber_not_red(store, make_claim):
    verdict = run(store, make_claim(company="NVDA", metric="eps_diluted", fiscal_year=2022, value=0.385, unit="USD_per_share"), 0.001)
    assert verdict.status is Status.UNSUPPORTED
    assert "possible_split_adjustment" in verdict.flags


def test_derived_fact_is_cited_with_its_formula(store, make_claim):
    verdict = run(store, make_claim(company="AMZN", metric="gross_profit", fiscal_year=2023, value=270.0e9), 1e8)
    assert verdict.status is Status.VERIFIED
    assert "derived as Revenue - Cost of revenue" in verdict.calculation


# ---- computed claims ---------------------------------------------------------

def test_growth_verified_and_shows_working(store, make_claim):
    verdict = run(store, make_claim(kind="growth_pct", value=2.0, unit="pct"), 0.1)
    assert verdict.status is Status.VERIFIED
    assert verdict.expected == pytest.approx(2.022, abs=0.001)
    assert "= +2.02%" in verdict.calculation
    assert len(verdict.sources) == 2


def test_growth_wrong_value_and_wrong_period(store, make_claim):
    assert run(store, make_claim(kind="growth_pct", value=12.0, unit="pct"), 1).status is Status.WRONG_VALUE
    shifted = run(store, make_claim(kind="growth_pct", value=6.4, unit="pct"), 0.1)
    assert shifted.status is Status.WRONG_PERIOD and shifted.matched_year == 2025


def test_growth_direction_hint(store, make_claim):
    verdict = run(store, make_claim(kind="growth_pct", fiscal_year=2023, value=2.8, unit="pct"), 0.1)
    assert verdict.status is Status.WRONG_VALUE
    assert verdict.hint == "The size is right but the direction is wrong."
    assert run(store, make_claim(kind="growth_pct", fiscal_year=2023, value=-2.8, unit="pct"), 0.1).status is Status.VERIFIED


def test_multi_year_growth_and_cagr(store, make_claim):
    total = run(store, make_claim(kind="growth_pct", compare_year=2021, value=6.9, unit="pct"), 0.1)
    assert total.status is Status.VERIFIED
    cagr = run(store, make_claim(kind="cagr_pct", compare_year=2021, value=2.2, unit="pct"), 0.1)
    assert cagr.status is Status.VERIFIED
    assert run(store, make_claim(kind="cagr_pct", compare_year=2024, value=2.2, unit="pct")).status is Status.UNSUPPORTED


def test_change_in_absolute_terms(store, make_claim):
    verdict = run(store, make_claim(kind="change_abs", value=7.75e9), 1e7)
    assert verdict.status is Status.VERIFIED
    assert verdict.expected == pytest.approx(7_750_000_000)


def test_margin(store, make_claim):
    gross = run(store, make_claim(kind="margin_pct", metric="gross_profit", value=46.2, unit="pct"), 0.1)
    assert gross.status is Status.VERIFIED and gross.expected == pytest.approx(46.206, abs=0.001)
    wrong = run(store, make_claim(kind="margin_pct", metric="gross_profit", value=52.0, unit="pct"), 0.1)
    assert wrong.status is Status.WRONG_VALUE
    other_base = run(store, make_claim(kind="margin_pct", metric="total_liabilities", denominator="total_assets", value=84.4, unit="pct"), 0.1)
    assert other_base.status is Status.VERIFIED
    assert run(store, make_claim(kind="margin_pct", metric="revenue", value=100, unit="pct")).status is Status.UNSUPPORTED


def test_ratio_within_and_across_companies(store, make_claim):
    within = run(store, make_claim(kind="ratio", metric="total_assets", denominator="total_liabilities", value=1.18, unit="x"), 0.01)
    assert within.status is Status.VERIFIED
    across = run(store, make_claim(kind="ratio", compare_company="MSFT", value=1.6, unit="x"), 0.1)
    assert across.status is Status.VERIFIED and len(across.filing_urls) == 2
    assert run(store, make_claim(kind="ratio", compare_company="MSFT", value=2.4, unit="x"), 0.1).status is Status.WRONG_VALUE
    assert run(store, make_claim(kind="ratio", value=1.0, unit="x")).status is Status.UNSUPPORTED
    assert run(store, make_claim(kind="ratio", compare_company="Initech", value=1.0, unit="x")).status is Status.UNSUPPORTED


def test_cross_company_growth(store, make_claim):
    verdict = run(store, make_claim(kind="growth_pct", compare_company="MSFT", value=59.5, unit="pct"), 0.1)
    assert verdict.status is Status.VERIFIED


def test_per_share_growth_uses_one_filing_basis(store, make_claim):
    same = run(store, make_claim(company="NVDA", metric="eps_diluted", kind="growth_pct", fiscal_year=2023, value=-54.8, unit="pct"), 0.1)
    assert same.status is Status.VERIFIED
    assert "Both years taken as stated" in same.calculation
    far = run(store, make_claim(company="NVDA", metric="eps_diluted", kind="growth_pct", fiscal_year=2025, compare_year=2021, value=70, unit="pct"), 1)
    assert far.status is Status.UNSUPPORTED and "share basis" in far.reason


def test_unit_conflict_between_claim_and_text(store, make_claim):
    verdict = run(store, make_claim(), 1e8, token_unit="pct")
    assert verdict.status is Status.UNSUPPORTED
    assert run(store, make_claim(kind="growth_pct", value=2.0, unit="pct"), 0.1, token_unit="USD").status is Status.UNSUPPORTED
    assert run(store, make_claim(kind="ratio", compare_company="MSFT", value=1.6, unit="x"), 0.1, token_unit="USD").status is Status.UNSUPPORTED


def test_same_inputs_give_the_same_verdict(store, make_claim):
    claim = make_claim(value=500e9)
    assert run(store, claim, 1e9).model_dump() == run(store, claim, 1e9).model_dump()


# ---- architecture guard ------------------------------------------------------

@pytest.mark.parametrize("module", ["check", "coverage", "numparse", "sanitize", "edgar", "fmt", "models"])
def test_checking_modules_never_import_an_llm_client(module):
    source = (Path(check.__file__).parent / f"{module}.py").read_text()
    imported = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.add((node.module or "").split(".")[0])
            imported.update(alias.name for alias in node.names)
    assert not imported & {"anthropic", "openai", "llm", "extract", "naive", "subprocess"}

"""The checker. Pure Python: a lookup plus arithmetic, no LLM, no network.

``check_claim`` takes one structured claim and the local fact store and returns
a verdict with the source facts, the calculation as text, and filing links.
The same inputs always give the same verdict, so any verdict can be replayed.
"""
from __future__ import annotations

from dataclasses import dataclass

from . import fmt
from .edgar import METRICS, PUBLIC_METRICS, Fact, FactStore
from .models import Claim, Status, TokenRef, Verdict

PCT_KINDS = ("growth_pct", "margin_pct", "cagr_pct")
PER_SHARE_METRICS = ("eps_diluted", "eps_basic", "shares_outstanding")
SCALE_FACTORS = (1e3, 1e6, 1e9, 1e12)
SPLIT_FACTORS = (2, 3, 4, 5, 6, 7, 8, 10, 15, 20, 25, 30, 40, 50)
PERIOD_SEARCH_RADIUS = 5


@dataclass(frozen=True)
class Policy:
    """Tolerances. Recorded in every certificate so a verdict can be replayed."""

    pct_points: float = 0.5            # growth and margin claims: percentage points
    relative: float = 0.005            # used when the stated precision is unknown
    # Optional leniency: also accept a truncated figure ("94.9" for 94.97) when the last digit is at most
    # this share of the figure. Off by default: a truncated figure can equal a neighbouring year's value.
    truncation_max_step: float = 0.0

    def to_dict(self) -> dict:
        return {
            "pct_points": self.pct_points,
            "relative": self.relative,
            "truncation_max_step": self.truncation_max_step,
        }


DEFAULT_POLICY = Policy()


@dataclass(frozen=True)
class Computed:
    value: float
    unit: str
    sources: tuple[Fact, ...]
    calculation: str
    note: str | None = None


@dataclass(frozen=True)
class Missing:
    reason: str


def metric_unit(metric: str) -> str:
    spec = METRICS[metric]
    if spec.unit == "USD/shares":
        return "USD_per_share"
    return spec.unit


def _label(metric: str) -> str:
    return METRICS[metric].label if metric in METRICS else metric


def _cite(fact: Fact) -> str:
    origin = f"{fact.form} filed {fact.filed}, accession {fact.accn}"
    if fact.derived:
        parts = " and ".join(f"{_label(c.metric)} {fmt.full(c.value, metric_unit(c.metric))}" for c in fact.components)
        return f"derived as {fact.formula} from {parts} ({origin})"
    return f"{origin}, XBRL tag {fact.tag}"


def _fact_line(fact: Fact) -> str:
    return (
        f"{fact.ticker} {_label(fact.metric)} FY{fact.fiscal_year} = "
        f"{fmt.full(fact.value, metric_unit(fact.metric))} [{_cite(fact)}]"
    )


# --------------------------------------------------------------------------
# Tolerance
# --------------------------------------------------------------------------

def matches(claimed: float, actual: float, quantum: float | None, kind: str, policy: Policy = DEFAULT_POLICY) -> bool:
    """Does the claimed figure agree with the filed one?

    Percentages: within ``policy.pct_points``. Everything else: the filed
    figure must round to the claimed one at the precision the text states
    ("$391bn" accepts 390.5 to 391.5bn). With no known precision, 0.5%
    relative. A policy can opt in to accepting truncated figures as well.
    """
    if kind in PCT_KINDS:
        return abs(claimed - actual) <= policy.pct_points + 1e-9
    if actual < 0 < claimed:
        claimed = -claimed  # a loss stated as a magnitude: "net loss of $2.7bn"
    diff = abs(claimed - actual)
    if not quantum:
        return diff <= policy.relative * abs(actual) + 1e-12
    slack = quantum * 1e-9
    if diff <= quantum / 2 + slack:
        return True
    same_sign = (claimed >= 0) == (actual >= 0)
    fine_enough = claimed != 0 and quantum / abs(claimed) <= policy.truncation_max_step
    truncated = same_sign and abs(actual) >= abs(claimed) and abs(actual) - abs(claimed) < quantum - slack
    return fine_enough and truncated


def tolerance_text(quantum: float | None, kind: str, unit: str, policy: Policy = DEFAULT_POLICY) -> str:
    if kind in PCT_KINDS:
        return f"tolerance ±{policy.pct_points:g} percentage points"
    if not quantum:
        return f"tolerance ±{policy.relative * 100:g}% (precision not stated)"
    return f"tolerance: rounding at the stated precision ({fmt.display(quantum, unit) if unit != 'USD' else fmt.money(quantum)})"


# --------------------------------------------------------------------------
# Expected values
# --------------------------------------------------------------------------

def _need(store: FactStore, ticker: str, metric: str, year: int) -> Fact | Missing:
    fact = store.get_fact(ticker, metric, year)
    if fact is None:
        if year not in store.calendar(ticker):
            return Missing(f"No annual filing on record for {ticker} FY{year}.")
        return Missing(f"{ticker} does not report {_label(metric)} for FY{year} in the filings on record.")
    return fact


def _pair(store: FactStore, earlier: Fact, later: Fact) -> tuple[float, float, str | None] | Missing:
    """Two periods of one metric, on one filing's basis when a filing has both."""
    if earlier.ticker == later.ticker and earlier.metric == later.metric:
        basis = store.common_basis(earlier, later)
        if basis is not None:
            va, vb, row = basis
            note = None
            if va != earlier.value or vb != later.value:
                note = f"Both years taken as stated in the {row.form} filed {row.filed} (accession {row.accn})."
            return va, vb, note
        if earlier.metric in PER_SHARE_METRICS:
            return Missing(
                f"No single filing states {_label(earlier.metric)} for both FY{earlier.fiscal_year} and "
                f"FY{later.fiscal_year}, so the share basis may differ (stock splits)."
            )
    return earlier.value, later.value, None


def compute(store: FactStore, claim: Claim, ticker: str, year: int, compare_year: int | None) -> Computed | Missing:
    """The figure the filings imply for this claim, with its working shown."""
    metric, kind = claim.metric, claim.kind
    unit = metric_unit(metric)
    if kind == "absolute":
        fact = _need(store, ticker, metric, year)
        if isinstance(fact, Missing):
            return fact
        return Computed(fact.value, unit, (fact,), _fact_line(fact))

    if kind in ("growth_pct", "cagr_pct", "change_abs"):
        other_ticker = store.resolve(claim.compare_company) if claim.compare_company else ticker
        if other_ticker is None:
            return Missing(f"No filing data for {claim.compare_company}.")
        base_year = compare_year if compare_year is not None else (year if claim.compare_company else year - 1)
        later = _need(store, ticker, metric, year)
        earlier = _need(store, other_ticker, metric, base_year)
        if isinstance(later, Missing):
            return later
        if isinstance(earlier, Missing):
            return earlier
        pair = _pair(store, earlier, later)
        if isinstance(pair, Missing):
            return pair
        va, vb, note = pair
        lines = f"{_fact_line(earlier)}\n{_fact_line(later)}"
        a_txt, b_txt = fmt.full(va, unit), fmt.full(vb, unit)
        if kind == "change_abs":
            change = vb - va
            return Computed(change, unit, (earlier, later), f"{lines}\n{b_txt} - {a_txt} = {fmt.full(change, unit)}", note)
        if va == 0:
            return Missing("The base-year figure is zero, so a percentage change is undefined.")
        if kind == "growth_pct":
            growth = (vb - va) / abs(va) * 100.0
            return Computed(growth, "pct", (earlier, later),
                            f"{lines}\n({b_txt} - {a_txt}) / |{a_txt}| = {growth:+.2f}%", note)
        years = year - base_year
        if years < 1 or va <= 0 or vb <= 0:
            return Missing("A compound growth rate needs at least one year between two positive figures.")
        cagr = ((vb / va) ** (1.0 / years) - 1.0) * 100.0
        return Computed(cagr, "pct", (earlier, later),
                        f"{lines}\n({b_txt} / {a_txt})^(1/{years}) - 1 = {cagr:+.2f}%", note)

    if kind == "margin_pct":
        denominator = claim.denominator or "revenue"
        if denominator not in METRICS or denominator == metric:
            return Missing(f"A margin needs two different quantities (got {metric} over {denominator}).")
        num = _need(store, ticker, metric, year)
        den = _need(store, ticker, denominator, year)
        if isinstance(num, Missing):
            return num
        if isinstance(den, Missing):
            return den
        if den.value == 0:
            return Missing("The denominator is zero.")
        ratio = num.value / den.value * 100.0
        return Computed(ratio, "pct", (num, den),
                        f"{_fact_line(num)}\n{_fact_line(den)}\n"
                        f"{fmt.full(num.value, metric_unit(metric))} / {fmt.full(den.value, metric_unit(denominator))} = {ratio:.2f}%")

    if kind == "ratio":
        other_ticker = store.resolve(claim.compare_company) if claim.compare_company else ticker
        if other_ticker is None:
            return Missing(f"No filing data for {claim.compare_company}.")
        denominator = claim.denominator or metric
        other_year = compare_year if compare_year is not None else year
        if denominator not in METRICS:
            return Missing(f"No source data for {denominator}.")
        if (other_ticker, denominator, other_year) == (ticker, metric, year):
            return Missing("A ratio needs a second quantity to compare with.")
        num = _need(store, ticker, metric, year)
        den = _need(store, other_ticker, denominator, other_year)
        if isinstance(num, Missing):
            return num
        if isinstance(den, Missing):
            return den
        if den.value == 0:
            return Missing("The denominator is zero.")
        ratio = num.value / den.value
        return Computed(ratio, "x", (num, den),
                        f"{_fact_line(num)}\n{_fact_line(den)}\n"
                        f"{fmt.full(num.value, metric_unit(metric))} / {fmt.full(den.value, metric_unit(denominator))} = {ratio:.3f}x")

    return Missing(f"Unknown claim kind: {kind}.")


# --------------------------------------------------------------------------
# Verdicts
# --------------------------------------------------------------------------

def _verdict(status: Status, claim: Claim, claimed: float, unit: str, reason: str, *,
             computed: Computed | None = None, token: TokenRef | None = None, **extra) -> Verdict:
    sources = [f.to_dict() for f in computed.sources] if computed else []
    urls: list[str] = []
    for source in computed.sources if computed else ():
        for fact in (source, *source.components):
            if fact.url not in urls:
                urls.append(fact.url)
    return Verdict(
        status=status,
        claim=claim,
        token=token,
        claimed=claimed,
        claimed_display=fmt.display(claimed, unit),
        expected=computed.value if computed else None,
        expected_display=fmt.display(computed.value, computed.unit) if computed else None,
        reason=reason,
        calculation=computed.calculation if computed else "",
        sources=sources,
        filing_urls=urls,
        **extra,
    )


def _deviation(claimed: float, actual: float, kind: str) -> str:
    if kind in PCT_KINDS:
        return f"{abs(claimed - actual):.2f} percentage points {'too high' if claimed > actual else 'too low'}"
    if actual == 0:
        return "the filed figure is zero"
    if actual < 0 < claimed:
        claimed = -claimed
    pct = (claimed - actual) / abs(actual) * 100.0
    return f"{abs(pct):.1f}% {'too high' if pct > 0 else 'too low'}"


def _display_unit(claim: Claim) -> str:
    if claim.kind in PCT_KINDS:
        return "pct"
    if claim.kind == "ratio":
        return "x"
    if claim.metric in METRICS:
        return metric_unit(claim.metric)
    return claim.unit


def _unit_conflict(kind: str, token_unit: str | None) -> str | None:
    if token_unit is None:
        return None
    if kind in PCT_KINDS and token_unit not in ("pct", "number"):
        return "The claim is a percentage but the number in the text is not."
    if kind in ("absolute", "change_abs") and token_unit in ("pct", "pp", "bps", "x"):
        return "The claim is an amount but the number in the text is a percentage or multiple."
    if kind == "ratio" and token_unit in ("USD", "shares", "pp", "bps"):
        return "The claim is a ratio but the number in the text is an amount."
    return None


def _year_candidates(store: FactStore, ticker: str, year: int, radius: int | None) -> list[int]:
    years = [y for y in store.fiscal_years(ticker) if y != year and (radius is None or abs(y - year) <= radius)]
    return sorted(years, key=lambda y: (abs(y - year), -y))


def _find_other_period(store, claim, ticker, year, compare_year, value, quantum, policy,
                       radius: int | None = PERIOD_SEARCH_RADIUS) -> tuple[int, Computed] | None:
    for other in _year_candidates(store, ticker, year, radius):
        shift = other - year
        shifted_compare = compare_year + shift if compare_year is not None else None
        candidate = compute(store, claim, ticker, other, shifted_compare)
        if isinstance(candidate, Computed) and matches(value, candidate.value, quantum, claim.kind, policy):
            return other, candidate
    return None


def _other_metric_hint(store, claim, ticker, year, value, quantum, policy) -> str | None:
    unit = metric_unit(claim.metric)
    for metric in PUBLIC_METRICS:
        if metric == claim.metric or metric_unit(metric) != unit:
            continue
        fact = store.get_fact(ticker, metric, year)
        if fact is not None and matches(value, fact.value, quantum, "absolute", policy):
            return f"The figure matches {ticker} {_label(metric)} for FY{year}, not {_label(claim.metric)}."
    return None


def check_claim(
    claim: Claim,
    store: FactStore,
    *,
    value: float | None = None,
    quantum: float | None = None,
    token: TokenRef | None = None,
    token_unit: str | None = None,
    policy: Policy = DEFAULT_POLICY,
) -> Verdict:
    """Check one claim against the filings.

    ``value`` and ``quantum`` come from the number as written in the report
    (parsed by code). When omitted, the claim's own value is used.
    """
    claimed = claim.value if value is None else value
    unit = _display_unit(claim)
    base = dict(token=token)

    if claim.metric not in PUBLIC_METRICS:
        return _verdict(Status.UNSUPPORTED, claim, claimed, unit,
                        "This metric is not covered by the filing data (v1 covers headline annual figures).", **base)
    ticker = store.resolve(claim.company)
    if ticker is None:
        return _verdict(Status.UNSUPPORTED, claim, claimed, unit,
                        f"No filing data on record for '{claim.company}'.", **base)
    conflict = _unit_conflict(claim.kind, token_unit)
    if conflict:
        return _verdict(Status.UNSUPPORTED, claim, claimed, unit, conflict, **base)

    kind = claim.kind
    if claim.fiscal_year is None:
        hint = None
        for year in sorted(store.fiscal_years(ticker), reverse=True):
            candidate = compute(store, claim, ticker, year, None)
            if isinstance(candidate, Computed) and matches(claimed, candidate.value, quantum, kind, policy):
                hint = f"The figure matches FY{year}."
                break
        return _verdict(Status.UNSUPPORTED, claim, claimed, unit,
                        "The report does not say which fiscal year this figure is for.", hint=hint, **base)

    year, compare_year = claim.fiscal_year, claim.compare_year
    computed = compute(store, claim, ticker, year, compare_year)
    if isinstance(computed, Missing):
        hint = None
        if ticker and kind == "absolute":
            found = _find_other_period(store, claim, ticker, year, compare_year, claimed, quantum, policy, radius=None)
            if found:
                hint = f"The figure matches FY{found[0]}."
        return _verdict(Status.UNSUPPORTED, claim, claimed, unit, computed.reason, hint=hint, **base)

    tolerance = tolerance_text(quantum, kind, unit, policy)
    working = Computed(
        computed.value, computed.unit, computed.sources,
        f"{computed.calculation}\nReport says {fmt.display(claimed, unit)}; {tolerance}."
        + (f"\n{computed.note}" if computed.note else ""),
    )

    if matches(claimed, computed.value, quantum, kind, policy):
        reason = "Matches the filed figure." if kind == "absolute" else "Matches the figure computed from the filings."
        return _verdict(Status.VERIFIED, claim, claimed, unit, reason, computed=working, **base)

    if kind == "absolute":
        fact = computed.sources[0]
        for row in fact.other_reported:
            if matches(claimed, row.value, quantum, kind, policy):
                reason = (
                    f"Matches the figure as filed in the {row.form} of {row.filed} (accession {row.accn}). "
                    f"A later filing restates it to {fmt.display(fact.value, unit)}."
                )
                return _verdict(Status.VERIFIED, claim, claimed, unit, reason, computed=working,
                                flags=["as_originally_filed"], **base)

    if kind in ("absolute", "change_abs") and unit != "USD_per_share" and computed.value != 0:
        for factor in SCALE_FACTORS:
            for scaled, direction in ((claimed / factor, factor), (claimed * factor, 1.0 / factor)):
                scaled_quantum = quantum / direction if quantum else None
                if matches(scaled, computed.value, scaled_quantum, kind, policy):
                    reason = (
                        f"Off by a factor of {factor:,.0f}: the filings give {fmt.display(computed.value, unit)}, "
                        f"the report says {fmt.display(claimed, unit)} (a units mix-up such as millions vs billions)."
                    )
                    return _verdict(Status.SCALE_ERROR, claim, claimed, unit, reason, computed=working,
                                    scale_factor=direction, **base)

    found = _find_other_period(store, claim, ticker, year, compare_year, claimed, quantum, policy)
    if found:
        other_year, _ = found
        reason = (
            f"{fmt.display(claimed, unit)} is the FY{other_year} figure. "
            f"For FY{year} the filings give {fmt.display(computed.value, unit)}."
        )
        return _verdict(Status.WRONG_PERIOD, claim, claimed, unit, reason, computed=working,
                        matched_year=other_year, **base)

    if kind == "absolute" and claim.metric in PER_SHARE_METRICS and claimed and computed.value:
        ratio = computed.value / claimed
        for factor in SPLIT_FACTORS:
            if abs(ratio - factor) / factor < 0.02 or abs(ratio - 1.0 / factor) * factor < 0.02:
                reason = (
                    f"The filings on record state {fmt.display(computed.value, unit)} for FY{year}. The report's "
                    f"figure differs by a factor of {factor}, which is consistent with a {factor}-for-1 stock split "
                    f"adjustment that no filing on record applies to this year."
                )
                return _verdict(Status.UNSUPPORTED, claim, claimed, unit, reason, computed=working,
                                flags=["possible_split_adjustment"], **base)

    hint = None
    if kind == "absolute":
        hint = _other_metric_hint(store, claim, ticker, year, claimed, quantum, policy)
    elif kind in ("growth_pct", "cagr_pct") and matches(-claimed, computed.value, quantum, kind, policy):
        hint = "The size is right but the direction is wrong."
    reason = (
        f"The filings give {fmt.display(computed.value, unit)}; the report says {fmt.display(claimed, unit)} "
        f"({_deviation(claimed, computed.value, kind)})."
    )
    return _verdict(Status.WRONG_VALUE, claim, claimed, unit, reason, computed=working, hint=hint, **base)

import pytest

from backend.numparse import find_tokens, parse_number, words_to_number


def values(text, **kw):
    return [(t.text, t.value, t.unit) for t in find_tokens(text, **kw)]


@pytest.mark.parametrize("text,value,unit,quantum", [
    ("$391.0 billion", 391.0e9, "USD", 1e8),
    ("$391bn", 391e9, "USD", 1e9),
    ("US$93.7B", 93.7e9, "USD", 1e8),
    ("$96.8 B", 96.8e9, "USD", 1e8),
    ("$31,370m", 31.37e9, "USD", 1e6),
    ("$2.9T", 2.9e12, "USD", 1e11),
    ("$6.08", 6.08, "USD", 0.01),
    ("391,035 million", 391.035e9, "number", 1e6),
    ("46.2 percent", 46.2, "pct", 0.1),
    ("31.5 per cent", 31.5, "pct", 0.1),
    ("18.2 %", 18.2, "pct", 0.1),
    ("150 basis points", 150, "bps", 1),
    ("1.2 percentage points", 1.2, "pp", 0.1),
    ("1.6x", 1.6, "x", 0.1),
    ("2.3 times", 2.3, "x", 0.1),
    ("15.4 billion diluted shares", 15.4e9, "shares", 1e8),
    ("391 billion dollars", 391e9, "USD", 1e9),
    ("-$2.7bn", -2.7e9, "USD", 1e8),
    ("$(2.7) billion", -2.7e9, "USD", 1e8),
    ("−3.2%", -3.2, "pct", 0.1),
    ("$16.4bn", 16.4e9, "USD", 1e8),
])
def test_single_number(text, value, unit, quantum):
    token = parse_number(text)
    assert token is not None
    assert token.value == pytest.approx(value)
    assert token.unit == unit
    assert token.quantum == pytest.approx(quantum)
    assert token.text == text


def test_offsets_point_at_the_number():
    text = "Apple reported revenue of $391.0 billion in fiscal 2024, up 2.0% from $383.3bn."
    tokens = find_tokens(text)
    assert [t.text for t in tokens] == ["$391.0 billion", "2.0%", "$383.3bn"]
    for token in tokens:
        assert text[token.start:token.end] == token.text


@pytest.mark.parametrize("text", [
    "The 10-K was filed on November 1, 2024 for the year ended September 28, 2024.",
    "See Note 12 and the iPhone 16 launch in Q4 2024 (FY24, fiscal 2024, 2021-2024).",
    "Over 3 years and a 5-year period, across 12 months.",
    "Filed 2024-11-01, see https://www.sec.gov/Archives/edgar/data/320193/000032019324000123/x.htm",
    "In 2024 the company announced a 4-for-1 stock split.",
    "1. First point\n2. Second point (1)",
    "one of the three segments",
])
def test_years_dates_and_identifiers_are_not_figures(text):
    assert find_tokens(text) == []


def test_number_with_thousands_separator_is_not_a_year():
    assert values("sold 2,024 units") == [("2,024", 2024.0, "number")]


def test_bare_numbers_are_kept_so_nothing_slips_through():
    assert [t.text for t in find_tokens("EPS of 6.08 with 164,000 employees and revenue of 500")] == ["6.08", "164,000", "500"]


def test_spelled_out_numbers():
    tokens = find_tokens("Revenue reached five hundred billion dollars, growth of twenty percent, about three point five trillion.")
    assert [(t.value, t.unit, t.spelled) for t in tokens] == [
        (5e11, "USD", True), (20.0, "pct", True), (3.5e12, "number", True)]
    assert tokens[0].quantum == 1e9


def test_spaced_digits_are_caught_and_marked():
    token = find_tokens("revenue of 5 0 0 bn this year")[0]
    assert (token.value, token.obfuscated, token.text) == (5e11, True, "5 0 0 bn")


def test_anti_evasion_passes_can_be_switched_off():
    text = "revenue of five hundred billion dollars, or 5 0 0 bn"
    assert len(find_tokens(text)) == 2
    assert find_tokens(text, spelled=False, spaced=False) != find_tokens(text)
    assert all(not t.spelled and not t.obfuscated for t in find_tokens(text, spelled=False, spaced=False))


@pytest.mark.parametrize("words,expected", [
    ("five hundred billion", (5e11, 1e9)),
    ("three hundred ninety-one billion", (391e9, 1e9)),
    ("two thousand five hundred", (2500.0, 1.0)),
    ("a hundred", (100.0, 1.0)),
    ("twenty-one", (21.0, 1.0)),
    ("three point five trillion", (3.5e12, 1e11)),
    ("six point zero eight", (6.08, 0.01)),
])
def test_words_to_number(words, expected):
    assert words_to_number(words) == pytest.approx(expected)


def test_words_to_number_rejects_nonsense():
    assert words_to_number("three point") is None
    assert words_to_number("three point five apples") is None


def test_parse_number_needs_exactly_one():
    assert parse_number("no numbers here") is None
    assert parse_number("$1bn and $2bn") is None


def test_range_and_parenthetical_numbers():
    assert [t.text for t in find_tokens("between 5% and 10%")] == ["5%", "10%"]
    assert [t.value for t in find_tokens("Revenue ($391.0 billion) rose")] == [391.0e9]

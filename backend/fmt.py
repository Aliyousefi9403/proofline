"""Number formatting for explanations. Pure Python."""
from __future__ import annotations


def money(value: float) -> str:
    """Compact currency: 391035000000 -> '$391.035bn'."""
    sign = "-" if value < 0 else ""
    a = abs(value)
    if a >= 1e12:
        return f"{sign}${a / 1e12:,.3f}tn"
    if a >= 1e9:
        return f"{sign}${a / 1e9:,.3f}bn"
    if a >= 1e6:
        return f"{sign}${a / 1e6:,.1f}m"
    return f"{sign}${a:,.2f}"


def money_full(value: float) -> str:
    sign = "-" if value < 0 else ""
    a = abs(value)
    return f"{sign}${a:,.0f}" if a >= 1000 else f"{sign}${a:,.2f}"


def count(value: float) -> str:
    a = abs(value)
    sign = "-" if value < 0 else ""
    if a >= 1e9:
        return f"{sign}{a / 1e9:,.3f}bn"
    if a >= 1e6:
        return f"{sign}{a / 1e6:,.1f}m"
    return f"{sign}{a:,.0f}"


def display(value: float, unit: str) -> str:
    if unit == "USD":
        return money(value)
    if unit == "USD_per_share":
        return f"{'-' if value < 0 else ''}${abs(value):,.2f}"
    if unit == "pct":
        return f"{value:+.2f}%" if value < 0 else f"{value:.2f}%"
    if unit == "x":
        return f"{value:.2f}x"
    if unit == "shares":
        return f"{count(value)} shares"
    return f"{value:,.4g}"


def full(value: float, unit: str) -> str:
    if unit == "USD":
        return money_full(value)
    if unit == "shares":
        return f"{value:,.0f} shares"
    return display(value, unit)

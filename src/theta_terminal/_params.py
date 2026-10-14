"""Normalize friendly Python arguments into the strings Terminal v3 expects."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import TypeAlias

DateLike: TypeAlias = "date | datetime | str"
TimeLike: TypeAlias = "time | str"
StrikeLike: TypeAlias = "int | float | Decimal | str"
IntervalLike: TypeAlias = "timedelta | str"

_RIGHTS = {"c": "call", "call": "call", "p": "put", "put": "put", "both": "both"}


def norm_date(value: DateLike | None) -> str | None:
    """``date(2026, 5, 1)``, ``"2026-05-01"`` or ``"20260501"`` -> ``"20260501"``."""
    if value is None:
        return None
    if isinstance(value, (date, datetime)):
        return value.strftime("%Y%m%d")
    s = str(value).strip().replace("-", "")
    if len(s) != 8 or not s.isdigit():
        raise ValueError(f"expected a date or YYYY-MM-DD / YYYYMMDD string, got {value!r}")
    return s


def norm_time(value: TimeLike | None) -> str | None:
    """``time(9, 30)`` -> ``"09:30:00"``; strings pass through unchanged."""
    if value is None:
        return None
    if isinstance(value, time):
        return value.strftime("%H:%M:%S")
    return str(value)


def norm_strike(value: StrikeLike | None) -> str | None:
    """Strike in dollars -> ``"450.000"``. ``"*"`` (all strikes) passes through.

    Terminal v3 takes strikes in dollars. The v2-era thousandths-of-a-dollar
    encoding is deliberately not converted: ``450000`` means $450,000, because
    a large number is ambiguous for high-priced underlyings.
    """
    if value is None:
        return None
    if value == "*":
        return "*"
    try:
        strike = Decimal(str(value).strip())
    except ArithmeticError:
        strike = None
    if strike is None or not strike.is_finite() or strike <= 0:
        raise ValueError(f"expected a positive strike in dollars or '*', got {value!r}")
    return f"{strike:.3f}"


def norm_right(value: str | None) -> str | None:
    """``"C"`` / ``"call"`` -> ``"call"``, ``"P"`` / ``"put"`` -> ``"put"``, ``"both"``."""
    if value is None:
        return None
    try:
        return _RIGHTS[str(value).lower()]
    except KeyError:
        raise ValueError(f"right must be call, put, or both; got {value!r}") from None


def norm_interval(value: IntervalLike | None) -> str | None:
    """``timedelta(minutes=5)`` -> ``"5m"``; strings such as ``"1m"`` pass through."""
    if value is None:
        return None
    if isinstance(value, timedelta):
        ms = round(value.total_seconds() * 1000)
        if ms <= 0:
            raise ValueError(f"interval must be positive, got {value!r}")
        if ms % 3_600_000 == 0:
            return f"{ms // 3_600_000}h"
        if ms % 60_000 == 0:
            return f"{ms // 60_000}m"
        if ms % 1000 == 0:
            return f"{ms // 1000}s"
        return f"{ms}ms"
    return str(value)


def parse_date(value: object) -> date | None:
    """Read a date from a response field: ``"2026-05-01"``, ``"20260501"`` or ``20260501``."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, (int, str)):
        s = str(value).strip()[:10]
        for fmt in ("%Y-%m-%d", "%Y%m%d"):
            try:
                return datetime.strptime(s, fmt).date()
            except ValueError:
                continue
    return None

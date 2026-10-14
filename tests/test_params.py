from __future__ import annotations

from datetime import date, datetime, time, timedelta
from decimal import Decimal

import pytest

from theta_terminal._params import (
    norm_date,
    norm_interval,
    norm_right,
    norm_strike,
    norm_time,
    parse_date,
)


@pytest.mark.parametrize(
    "value",
    [date(2026, 5, 1), datetime(2026, 5, 1, 15, 30), "2026-05-01", "20260501"],
)
def test_dates_become_yyyymmdd(value: object) -> None:
    assert norm_date(value) == "20260501"  # type: ignore[arg-type]


@pytest.mark.parametrize("value", ["5/1/2026", "2026-5-1", "tomorrow"])
def test_bad_dates_are_rejected(value: str) -> None:
    with pytest.raises(ValueError):
        norm_date(value)


def test_times() -> None:
    assert norm_time(time(9, 30)) == "09:30:00"
    assert norm_time("09:30:00") == "09:30:00"
    assert norm_time(None) is None


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (450, "450.000"),
        (452.5, "452.500"),
        ("452.5", "452.500"),
        (Decimal("7.25"), "7.250"),
        ("*", "*"),
    ],
)
def test_strikes_are_dollars_with_three_decimals(value: object, expected: str) -> None:
    assert norm_strike(value) == expected  # type: ignore[arg-type]


@pytest.mark.parametrize("value", ["abc", 0, -5, "inf", "nan"])
def test_bad_strikes_are_rejected(value: object) -> None:
    with pytest.raises(ValueError):
        norm_strike(value)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("value", "expected"),
    [("C", "call"), ("call", "call"), ("P", "put"), ("PUT", "put"), ("both", "both")],
)
def test_rights(value: str, expected: str) -> None:
    assert norm_right(value) == expected


def test_bad_right_is_rejected() -> None:
    with pytest.raises(ValueError):
        norm_right("straddle")


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (timedelta(minutes=1), "1m"),
        (timedelta(minutes=5), "5m"),
        (timedelta(seconds=30), "30s"),
        (timedelta(hours=1), "1h"),
        (timedelta(milliseconds=100), "100ms"),
        ("tick", "tick"),
    ],
)
def test_intervals(value: object, expected: str) -> None:
    assert norm_interval(value) == expected  # type: ignore[arg-type]


def test_zero_interval_is_rejected() -> None:
    with pytest.raises(ValueError):
        norm_interval(timedelta(0))


@pytest.mark.parametrize("value", ["2026-05-01", "20260501", 20260501, "2026-05-01T00:00:00"])
def test_parse_date(value: object) -> None:
    assert parse_date(value) == date(2026, 5, 1)


def test_parse_date_rejects_garbage() -> None:
    assert parse_date("SPY") is None
    assert parse_date(None) is None

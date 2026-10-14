from __future__ import annotations

import pytest

from theta_terminal._parse import decode_json, flatten
from theta_terminal.errors import InvalidResponse

CONTRACT = {"symbol": "SPY", "expiration": "2026-05-15", "strike": 500.0, "right": "CALL"}


def test_bare_list() -> None:
    assert flatten([{"a": 1}, {"a": 2}]) == [{"a": 1}, {"a": 2}]


def test_response_envelope() -> None:
    assert flatten({"response": [{"a": 1}]}) == [{"a": 1}]


def test_nested_contract_data_is_flattened_and_merged() -> None:
    payload = {
        "response": [
            {"contract": CONTRACT, "data": [{"bid": 1.0}, {"bid": 1.1}]},
            {"contract": {**CONTRACT, "right": "PUT"}, "data": [{"bid": 2.0}]},
        ]
    }
    rows = flatten(payload)
    assert rows == [
        {**CONTRACT, "bid": 1.0},
        {**CONTRACT, "bid": 1.1},
        {**CONTRACT, "right": "PUT", "bid": 2.0},
    ]


def test_tick_fields_win_over_contract_fields() -> None:
    rows = flatten({"response": [{"contract": {"x": "contract"}, "data": [{"x": "tick"}]}]})
    assert rows == [{"x": "tick"}]


@pytest.mark.parametrize(
    "payload",
    [
        {"response": None},
        {"response": "oops"},
        {"response": [1, "two", None]},
        {"response": [{"contract": "bad", "data": [{"a": 1}, "bad"]}]},
        "oops",
    ],
)
def test_malformed_shapes_do_not_raise(payload: object) -> None:
    rows = flatten(payload)
    assert all(isinstance(r, dict) for r in rows)


def test_empty_body() -> None:
    with pytest.raises(InvalidResponse, match="empty"):
        decode_json(b"  ", "application/json", "u")


def test_html_error_page() -> None:
    with pytest.raises(InvalidResponse, match="content-type"):
        decode_json(b"<html>Bad Gateway</html>", "text/html", "u")


def test_csv_from_old_terminal() -> None:
    with pytest.raises(InvalidResponse):
        decode_json(b"symbol,expiration\nSPY,20260515\n", "text/csv", "u")


def test_json_without_json_content_type_is_accepted() -> None:
    assert decode_json(b'{"response": []}', "text/plain", "u") == {"response": []}


def test_malformed_json() -> None:
    with pytest.raises(InvalidResponse, match="malformed"):
        decode_json(b'{"response": [', "application/json", "u")

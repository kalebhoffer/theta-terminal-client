from __future__ import annotations

from datetime import date, time, timedelta

import httpx
import pytest

from theta_terminal import (
    AsyncThetaTerminal,
    ConnectionPoolShutDown,
    InvalidResponse,
    TerminalHTTPError,
    TerminalUnavailable,
    ThetaTerminal,
    to_dataframe,
)

from .conftest import FakeTerminal, json_response

CONTRACT = {"symbol": "SPY", "expiration": "2026-05-15", "strike": 500.0, "right": "CALL"}


# ── wire format ─────────────────────────────────────────────────────────────


def test_every_request_asks_for_json_and_closes_the_connection(
    client: ThetaTerminal, terminal: FakeTerminal
) -> None:
    client.option_snapshot_quote("SPY", date(2026, 5, 15))
    request = terminal.requests[0]
    assert request.url.params["format"] == "json"
    assert request.headers["connection"] == "close"


def test_keep_alive_pool_is_disabled() -> None:
    client = ThetaTerminal()
    pool = client._http._transport._pool  # type: ignore[attr-defined]
    assert pool._max_keepalive_connections == 0
    client.close()


def test_snapshot_params(client: ThetaTerminal, terminal: FakeTerminal) -> None:
    client.option_snapshot_greeks("SPY", "2026-05-15", strike=500, right="C")
    assert terminal.requests[0].url.path == "/v3/option/snapshot/greeks/first_order"
    assert terminal.last_params == {
        "symbol": "SPY",
        "expiration": "20260515",
        "strike": "500.000",
        "right": "call",
        "format": "json",
    }


def test_snapshot_defaults_to_whole_chain(client: ThetaTerminal, terminal: FakeTerminal) -> None:
    client.option_snapshot_open_interest("SPY", date(2026, 5, 15))
    assert terminal.requests[0].url.path == "/v3/option/snapshot/open_interest"
    assert terminal.last_params["strike"] == "*"
    assert terminal.last_params["right"] == "both"


def test_history_params(client: ThetaTerminal, terminal: FakeTerminal) -> None:
    client.option_history_quote(
        "SPY",
        date(2026, 5, 15),
        interval=timedelta(minutes=1),
        date=date(2026, 5, 1),
        strike=500,
        right="put",
        start_time=time(9, 30),
        end_time="16:00:00",
    )
    assert terminal.requests[0].url.path == "/v3/option/history/quote"
    assert terminal.last_params == {
        "symbol": "SPY",
        "expiration": "20260515",
        "strike": "500.000",
        "right": "put",
        "interval": "1m",
        "date": "20260501",
        "start_time": "09:30:00",
        "end_time": "16:00:00",
        "format": "json",
    }


def test_history_date_range(client: ThetaTerminal, terminal: FakeTerminal) -> None:
    client.option_history_ohlc(
        "SPY", "20260515", interval="5m", start_date="2026-05-01", end_date="2026-05-08"
    )
    assert terminal.last_params["start_date"] == "20260501"
    assert terminal.last_params["end_date"] == "20260508"
    assert "date" not in terminal.last_params


@pytest.mark.parametrize(
    "dates",
    [
        {},
        {"start_date": "2026-05-01"},
        {"date": "2026-05-01", "start_date": "2026-05-01", "end_date": "2026-05-02"},
    ],
)
def test_history_requires_one_date_form(client: ThetaTerminal, dates: dict[str, str]) -> None:
    with pytest.raises(ValueError):
        client.option_history_ohlc("SPY", "2026-05-15", interval="1m", **dates)  # type: ignore[arg-type]


def test_stock_history(client: ThetaTerminal, terminal: FakeTerminal) -> None:
    client.stock_history_ohlc("SPY", interval="1m", date="2026-05-01")
    assert terminal.requests[0].url.path == "/v3/stock/history/ohlc"
    assert terminal.last_params == {
        "symbol": "SPY",
        "interval": "1m",
        "date": "20260501",
        "format": "json",
    }


def test_generic_get_drops_none(client: ThetaTerminal, terminal: FakeTerminal) -> None:
    terminal.queue(json_response({"response": [{"strike": 500.0}]}))
    rows = client.get("/v3/option/list/strikes", symbol="SPY", expiration="20260515", x=None)
    assert rows == [{"strike": 500.0}]
    assert terminal.last_params == {"symbol": "SPY", "expiration": "20260515", "format": "json"}


def test_base_url_trailing_slash(terminal: FakeTerminal) -> None:
    client = ThetaTerminal("http://theta:25503/", transport=httpx.MockTransport(terminal.handle))
    client.option_expirations("SPY")
    assert str(terminal.requests[0].url).startswith("http://theta:25503/v3/option/list/")


# ── responses ───────────────────────────────────────────────────────────────


def test_nested_history_rows_are_flattened(client: ThetaTerminal, terminal: FakeTerminal) -> None:
    terminal.queue(
        json_response({"response": [{"contract": CONTRACT, "data": [{"bid": 1.0}, {"bid": 1.05}]}]})
    )
    rows = client.option_history_quote("SPY", "2026-05-15", interval="1m", date="2026-05-01")
    assert rows == [{**CONTRACT, "bid": 1.0}, {**CONTRACT, "bid": 1.05}]


def test_expirations_are_deduplicated_sorted_and_filtered(
    client: ThetaTerminal, terminal: FakeTerminal
) -> None:
    terminal.queue(
        json_response(
            {
                "response": [
                    {"symbol": "AAPL", "expiration": "2026-06-18"},
                    {"symbol": "AAPL1", "expiration": "2026-06-18"},
                    {"symbol": "AAPLW", "expiration": "2026-06-18"},
                    {"symbol": "AAPL", "expiration": "2012-06-01"},
                    {"symbol": "AAPL", "expiration": 20260515},
                    {"symbol": "AAPL", "expiration": "not a date"},
                ]
            }
        )
    )
    assert client.option_expirations("AAPL", on_or_after=date(2026, 5, 1)) == [
        date(2026, 5, 15),
        date(2026, 6, 18),
    ]
    assert "on_or_after" not in terminal.last_params


def test_expirations_without_filter_keep_history(
    client: ThetaTerminal, terminal: FakeTerminal
) -> None:
    terminal.queue(json_response([{"expiration": "2026-06-18"}, {"expiration": "2012-06-01"}]))
    assert client.option_expirations("SPY") == [date(2012, 6, 1), date(2026, 6, 18)]


def test_472_means_no_data(client: ThetaTerminal, terminal: FakeTerminal) -> None:
    terminal.queue(httpx.Response(472, text="No data found for your request"))
    assert client.option_snapshot_quote("SPY", "2026-05-15") == []
    terminal.queue(httpx.Response(472, text="No data found for your request"))
    assert client.option_expirations("XYZ") == []
    assert len(terminal.requests) == 2  # no retries


def test_invalid_body_is_reported(client: ThetaTerminal, terminal: FakeTerminal) -> None:
    terminal.queue(
        httpx.Response(200, text="<html>502</html>", headers={"content-type": "text/html"})
    )
    with pytest.raises(InvalidResponse):
        client.option_snapshot_quote("SPY", "2026-05-15")


def test_to_dataframe(client: ThetaTerminal, terminal: FakeTerminal) -> None:
    terminal.queue(json_response({"response": [{"contract": CONTRACT, "data": [{"bid": 1.0}]}]}))
    frame = to_dataframe(client.option_snapshot_quote("SPY", "2026-05-15"))
    assert list(frame.columns) == ["symbol", "expiration", "strike", "right", "bid"]
    assert frame.loc[0, "bid"] == 1.0


# ── errors and retries ──────────────────────────────────────────────────────


def test_pool_shut_down_is_retried_then_raised(
    client: ThetaTerminal, terminal: FakeTerminal
) -> None:
    for _ in range(3):
        terminal.queue(httpx.Response(500, text="Connection pool shut down"))
    with pytest.raises(ConnectionPoolShutDown) as info:
        client.stock_history_ohlc("SPY", interval="1m", date="2026-05-01")
    assert info.value.status_code == 500
    assert len(terminal.requests) == 3


def test_transient_500_recovers(client: ThetaTerminal, terminal: FakeTerminal) -> None:
    terminal.queue(
        httpx.Response(500, text="Connection pool shut down"),
        json_response({"response": [{"bid": 1.0}]}),
    )
    assert client.get("/v3/option/snapshot/quote") == [{"bid": 1.0}]


def test_4xx_is_not_retried(client: ThetaTerminal, terminal: FakeTerminal) -> None:
    terminal.queue(httpx.Response(400, text="Invalid symbol"))
    with pytest.raises(TerminalHTTPError) as info:
        client.option_snapshot_quote("???", "2026-05-15")
    assert not isinstance(info.value, ConnectionPoolShutDown)
    assert info.value.status_code == 400
    assert "Invalid symbol" in str(info.value)
    assert len(terminal.requests) == 1


def test_connection_refused_is_retried_then_raised(terminal: FakeTerminal) -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    terminal.queue(refuse, refuse)
    client = ThetaTerminal(
        max_retries=1, retry_backoff=0, transport=httpx.MockTransport(terminal.handle)
    )
    with pytest.raises(TerminalUnavailable, match="Is the Terminal running"):
        client.option_expirations("SPY")
    assert len(terminal.requests) == 2


def test_read_timeout_is_not_retried(client: ThetaTerminal, terminal: FakeTerminal) -> None:
    def slow(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    terminal.queue(slow)
    with pytest.raises(TerminalUnavailable):
        client.option_expirations("SPY")
    assert len(terminal.requests) == 1


def test_context_manager(terminal: FakeTerminal) -> None:
    with ThetaTerminal(transport=httpx.MockTransport(terminal.handle)) as client:
        client.option_expirations("SPY")
    assert client._http.is_closed


# ── async ───────────────────────────────────────────────────────────────────


async def test_async_matches_sync(aclient: AsyncThetaTerminal, terminal: FakeTerminal) -> None:
    terminal.queue(json_response({"response": [{"contract": CONTRACT, "data": [{"delta": 0.5}]}]}))
    rows = await aclient.option_snapshot_greeks("SPY", date(2026, 5, 15), strike=500, right="C")
    assert rows == [{**CONTRACT, "delta": 0.5}]
    assert terminal.last_params["strike"] == "500.000"
    await aclient.aclose()


async def test_async_retries(aclient: AsyncThetaTerminal, terminal: FakeTerminal) -> None:
    terminal.queue(
        httpx.Response(500, text="Connection pool shut down"),
        json_response([{"expiration": "2026-05-15"}]),
    )
    assert await aclient.option_expirations("SPY") == [date(2026, 5, 15)]
    assert len(terminal.requests) == 2


async def test_async_concurrency_is_capped(terminal: FakeTerminal) -> None:
    import asyncio

    in_flight = 0
    peak = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal in_flight, peak
        in_flight += 1
        peak = max(peak, in_flight)
        await asyncio.sleep(0.01)
        in_flight -= 1
        return json_response({"response": []})

    async with AsyncThetaTerminal(
        max_concurrency=2, transport=httpx.MockTransport(handler)
    ) as client:
        await asyncio.gather(*(client.option_expirations("SPY") for _ in range(8)))
    assert peak == 2


def test_async_rejects_zero_concurrency() -> None:
    with pytest.raises(ValueError):
        AsyncThetaTerminal(max_concurrency=0)

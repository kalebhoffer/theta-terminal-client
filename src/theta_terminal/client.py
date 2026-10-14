"""Sync and async clients for the Theta Terminal v3 REST API.

Both clients share the request builders and response handling below; they
differ only in how the HTTP call is made and how retries wait.
"""

from __future__ import annotations

import asyncio
import time as _time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from typing import Any, Generic, TypeVar

import httpx

from ._params import (
    DateLike,
    IntervalLike,
    StrikeLike,
    TimeLike,
    norm_date,
    norm_interval,
    norm_right,
    norm_strike,
    norm_time,
    parse_date,
)
from ._parse import Row, decode_json, flatten
from .errors import (
    ConnectionPoolShutDown,
    TerminalHTTPError,
    TerminalUnavailable,
)

DEFAULT_BASE_URL = "http://127.0.0.1:25503"

# Terminal v3's status for "no data matched your request". It is not an
# error: an expired contract or a holiday legitimately has no data.
NO_DATA_STATUS = 472

_POOL_SHUT_DOWN = "connection pool shut down"


# ── request building ────────────────────────────────────────────────────────


T = TypeVar("T")


@dataclass(frozen=True)
class _Request(Generic[T]):
    path: str
    params: dict[str, str]
    post: Callable[[list[Row]], T]


def _rows(path: str, params: dict[str, str]) -> _Request[list[Row]]:
    return _Request(path, params, lambda rows: rows)


def _clean(params: dict[str, Any]) -> dict[str, str]:
    out = {k: str(v) for k, v in params.items() if v is not None}
    out["format"] = "json"  # v3 defaults to CSV
    return out


def _expirations_request(symbol: str, on_or_after: DateLike | None) -> _Request[list[date]]:
    floor = parse_date(norm_date(on_or_after)) if on_or_after is not None else None

    def post(rows: list[Row]) -> list[date]:
        # One row per option root, so AAPL, AAPL1 and AAPLW repeat the same
        # date. The list also runs back to 2012, so filtering matters.
        found = {parse_date(r.get("expiration")) for r in rows}
        return sorted(d for d in found if d is not None and (floor is None or d >= floor))

    return _Request("/v3/option/list/expirations", _clean({"symbol": symbol}), post)


def _snapshot_request(
    kind: str, symbol: str, expiration: DateLike, strike: StrikeLike, right: str
) -> _Request[list[Row]]:
    return _rows(
        f"/v3/option/snapshot/{kind}",
        _clean(
            {
                "symbol": symbol,
                "expiration": norm_date(expiration),
                "strike": norm_strike(strike),
                "right": norm_right(right),
            }
        ),
    )


def _option_history_request(
    kind: str,
    symbol: str,
    expiration: DateLike,
    *,
    interval: IntervalLike,
    date: DateLike | None,
    start_date: DateLike | None,
    end_date: DateLike | None,
    strike: StrikeLike,
    right: str,
    start_time: TimeLike | None,
    end_time: TimeLike | None,
) -> _Request[list[Row]]:
    _check_date_range(date, start_date, end_date)
    return _rows(
        f"/v3/option/history/{kind}",
        _clean(
            {
                "symbol": symbol,
                "expiration": norm_date(expiration),
                "strike": norm_strike(strike),
                "right": norm_right(right),
                "interval": norm_interval(interval),
                "date": norm_date(date),
                "start_date": norm_date(start_date),
                "end_date": norm_date(end_date),
                "start_time": norm_time(start_time),
                "end_time": norm_time(end_time),
            }
        ),
    )


def _stock_history_request(
    symbol: str,
    *,
    interval: IntervalLike,
    date: DateLike | None,
    start_date: DateLike | None,
    end_date: DateLike | None,
    start_time: TimeLike | None,
    end_time: TimeLike | None,
) -> _Request[list[Row]]:
    _check_date_range(date, start_date, end_date)
    return _rows(
        "/v3/stock/history/ohlc",
        _clean(
            {
                "symbol": symbol,
                "interval": norm_interval(interval),
                "date": norm_date(date),
                "start_date": norm_date(start_date),
                "end_date": norm_date(end_date),
                "start_time": norm_time(start_time),
                "end_time": norm_time(end_time),
            }
        ),
    )


def _check_date_range(day: DateLike | None, start: DateLike | None, end: DateLike | None) -> None:
    if day is not None and (start is not None or end is not None):
        raise ValueError("pass either date or start_date/end_date, not both")
    if day is None and (start is None or end is None):
        raise ValueError("pass date, or both start_date and end_date")


# ── response handling ───────────────────────────────────────────────────────


class _Retryable(Exception):
    def __init__(self, error: Exception) -> None:
        self.error = error


def _handle(response: httpx.Response, request: _Request[T]) -> T:
    url = str(response.request.url)
    status = response.status_code
    if status == NO_DATA_STATUS:
        return request.post([])
    if status >= 400:
        body = response.text
        error: TerminalHTTPError
        if status == 500 and _POOL_SHUT_DOWN in body.lower():
            error = ConnectionPoolShutDown(status, body, url)
        else:
            error = TerminalHTTPError(status, body, url)
        if status >= 500:
            raise _Retryable(error)
        raise error
    payload = decode_json(response.content, response.headers.get("content-type", ""), url)
    return request.post(flatten(payload))


def _unavailable(exc: httpx.TransportError, base_url: str) -> TerminalUnavailable:
    return TerminalUnavailable(
        f"could not reach the Theta Terminal at {base_url} ({type(exc).__name__}: {exc}). "
        "Is the Terminal running and logged in?"
    )


def _retryable_transport_error(exc: httpx.TransportError) -> bool:
    # A read timeout means the Terminal is working on a large request;
    # sending it again only adds load. Connection failures are worth a retry.
    return not isinstance(exc, httpx.ReadTimeout)


_LIMITS = httpx.Limits(max_keepalive_connections=0)
_HEADERS = {"Connection": "close"}


# ── clients ─────────────────────────────────────────────────────────────────


class ThetaTerminal:
    """Blocking client for a local Theta Terminal v3.

    Args:
        base_url: Where the Terminal's REST API listens.
        timeout: Seconds to wait for each request.
        max_retries: Extra attempts after a connection failure or HTTP 5xx.
        retry_backoff: Seconds before the first retry; doubles each time.
        transport: Optional httpx transport, mainly for tests.

    Each request opens a fresh connection: the Terminal answers reused
    keep-alive connections with spurious HTTP 500s.
    """

    def __init__(
        self,
        base_url: str = DEFAULT_BASE_URL,
        *,
        timeout: float = 30.0,
        max_retries: int = 2,
        retry_backoff: float = 0.5,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.max_retries = max_retries
        self.retry_backoff = retry_backoff
        self._http = httpx.Client(
            base_url=self.base_url,
            timeout=timeout,
            limits=_LIMITS,
            headers=_HEADERS,
            transport=transport,
        )

    def __enter__(self) -> ThetaTerminal:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def close(self) -> None:
        self._http.close()

    def _run(self, request: _Request[T]) -> T:
        attempt = 0
        while True:
            try:
                try:
                    response = self._http.get(request.path, params=request.params)
                except httpx.TransportError as exc:
                    error = _unavailable(exc, self.base_url)
                    if not _retryable_transport_error(exc):
                        raise error from exc
                    raise _Retryable(error) from exc
                return _handle(response, request)
            except _Retryable as retry:
                if attempt >= self.max_retries:
                    raise retry.error from retry.__cause__
                _time.sleep(self.retry_backoff * 2**attempt)
                attempt += 1

    def get(self, path: str, **params: Any) -> list[Row]:
        """Call any v3 endpoint and return flattened rows.

        Use this for endpoints without a dedicated method. Parameters are
        sent as given (``None`` values are dropped) plus ``format=json``.
        """
        return self._run(_rows(path, _clean(params)))

    def option_expirations(self, symbol: str, *, on_or_after: DateLike | None = None) -> list[date]:
        """Unique expiration dates for ``symbol``, oldest first.

        The Terminal returns every expiration since 2012; pass
        ``on_or_after=date.today()`` to keep only live ones.
        """
        return self._run(_expirations_request(symbol, on_or_after))

    def option_snapshot_quote(
        self, symbol: str, expiration: DateLike, *, strike: StrikeLike = "*", right: str = "both"
    ) -> list[Row]:
        """Latest NBBO quote for each matching contract."""
        return self._run(_snapshot_request("quote", symbol, expiration, strike, right))

    def option_snapshot_greeks(
        self, symbol: str, expiration: DateLike, *, strike: StrikeLike = "*", right: str = "both"
    ) -> list[Row]:
        """First-order Greeks, implied volatility, bid/ask and underlying price."""
        return self._run(_snapshot_request("greeks/first_order", symbol, expiration, strike, right))

    def option_snapshot_ohlc(
        self, symbol: str, expiration: DateLike, *, strike: StrikeLike = "*", right: str = "both"
    ) -> list[Row]:
        """Session OHLC and cumulative volume so far today."""
        return self._run(_snapshot_request("ohlc", symbol, expiration, strike, right))

    def option_snapshot_open_interest(
        self, symbol: str, expiration: DateLike, *, strike: StrikeLike = "*", right: str = "both"
    ) -> list[Row]:
        """Open interest as reported by OPRA each morning (prior session's close)."""
        return self._run(_snapshot_request("open_interest", symbol, expiration, strike, right))

    def option_history_quote(
        self,
        symbol: str,
        expiration: DateLike,
        *,
        interval: IntervalLike,
        date: DateLike | None = None,
        start_date: DateLike | None = None,
        end_date: DateLike | None = None,
        strike: StrikeLike = "*",
        right: str = "both",
        start_time: TimeLike | None = None,
        end_time: TimeLike | None = None,
    ) -> list[Row]:
        """Historical NBBO quotes sampled at ``interval``, one row per contract per tick."""
        return self._run(
            _option_history_request(
                "quote",
                symbol,
                expiration,
                interval=interval,
                date=date,
                start_date=start_date,
                end_date=end_date,
                strike=strike,
                right=right,
                start_time=start_time,
                end_time=end_time,
            )
        )

    def option_history_ohlc(
        self,
        symbol: str,
        expiration: DateLike,
        *,
        interval: IntervalLike,
        date: DateLike | None = None,
        start_date: DateLike | None = None,
        end_date: DateLike | None = None,
        strike: StrikeLike = "*",
        right: str = "both",
        start_time: TimeLike | None = None,
        end_time: TimeLike | None = None,
    ) -> list[Row]:
        """Historical OHLC bars at ``interval``."""
        return self._run(
            _option_history_request(
                "ohlc",
                symbol,
                expiration,
                interval=interval,
                date=date,
                start_date=start_date,
                end_date=end_date,
                strike=strike,
                right=right,
                start_time=start_time,
                end_time=end_time,
            )
        )

    def option_history_greeks(
        self,
        symbol: str,
        expiration: DateLike,
        *,
        interval: IntervalLike,
        date: DateLike | None = None,
        start_date: DateLike | None = None,
        end_date: DateLike | None = None,
        strike: StrikeLike = "*",
        right: str = "both",
        start_time: TimeLike | None = None,
        end_time: TimeLike | None = None,
    ) -> list[Row]:
        """Historical first-order Greeks at ``interval``.

        Each row includes ``underlying_price``, which makes this a way to get
        intraday underlying prices on tiers without historical stock data.
        """
        return self._run(
            _option_history_request(
                "greeks/first_order",
                symbol,
                expiration,
                interval=interval,
                date=date,
                start_date=start_date,
                end_date=end_date,
                strike=strike,
                right=right,
                start_time=start_time,
                end_time=end_time,
            )
        )

    def stock_history_ohlc(
        self,
        symbol: str,
        *,
        interval: IntervalLike,
        date: DateLike | None = None,
        start_date: DateLike | None = None,
        end_date: DateLike | None = None,
        start_time: TimeLike | None = None,
        end_time: TimeLike | None = None,
    ) -> list[Row]:
        """Historical stock OHLC bars. Not available on the free stock tier."""
        return self._run(
            _stock_history_request(
                symbol,
                interval=interval,
                date=date,
                start_date=start_date,
                end_date=end_date,
                start_time=start_time,
                end_time=end_time,
            )
        )


class AsyncThetaTerminal:
    """Asyncio client for a local Theta Terminal v3.

    Same methods and arguments as :class:`ThetaTerminal`, awaited. Requests
    are capped at ``max_concurrency`` in flight, because the Terminal starts
    failing when it is hit with many requests at once.
    """

    def __init__(
        self,
        base_url: str = DEFAULT_BASE_URL,
        *,
        timeout: float = 30.0,
        max_retries: int = 2,
        retry_backoff: float = 0.5,
        max_concurrency: int = 4,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if max_concurrency < 1:
            raise ValueError("max_concurrency must be at least 1")
        self.base_url = base_url.rstrip("/")
        self.max_retries = max_retries
        self.retry_backoff = retry_backoff
        self._slots = asyncio.Semaphore(max_concurrency)
        self._http = httpx.AsyncClient(
            base_url=self.base_url,
            timeout=timeout,
            limits=_LIMITS,
            headers=_HEADERS,
            transport=transport,
        )

    async def __aenter__(self) -> AsyncThetaTerminal:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._http.aclose()

    async def _run(self, request: _Request[T]) -> T:
        attempt = 0
        while True:
            try:
                async with self._slots:
                    try:
                        response = await self._http.get(request.path, params=request.params)
                    except httpx.TransportError as exc:
                        error = _unavailable(exc, self.base_url)
                        if not _retryable_transport_error(exc):
                            raise error from exc
                        raise _Retryable(error) from exc
                return _handle(response, request)
            except _Retryable as retry:
                if attempt >= self.max_retries:
                    raise retry.error from retry.__cause__
                await asyncio.sleep(self.retry_backoff * 2**attempt)
                attempt += 1

    async def get(self, path: str, **params: Any) -> list[Row]:
        """See :meth:`ThetaTerminal.get`."""
        return await self._run(_rows(path, _clean(params)))

    async def option_expirations(
        self, symbol: str, *, on_or_after: DateLike | None = None
    ) -> list[date]:
        """See :meth:`ThetaTerminal.option_expirations`."""
        return await self._run(_expirations_request(symbol, on_or_after))

    async def option_snapshot_quote(
        self, symbol: str, expiration: DateLike, *, strike: StrikeLike = "*", right: str = "both"
    ) -> list[Row]:
        """See :meth:`ThetaTerminal.option_snapshot_quote`."""
        return await self._run(_snapshot_request("quote", symbol, expiration, strike, right))

    async def option_snapshot_greeks(
        self, symbol: str, expiration: DateLike, *, strike: StrikeLike = "*", right: str = "both"
    ) -> list[Row]:
        """See :meth:`ThetaTerminal.option_snapshot_greeks`."""
        return await self._run(
            _snapshot_request("greeks/first_order", symbol, expiration, strike, right)
        )

    async def option_snapshot_ohlc(
        self, symbol: str, expiration: DateLike, *, strike: StrikeLike = "*", right: str = "both"
    ) -> list[Row]:
        """See :meth:`ThetaTerminal.option_snapshot_ohlc`."""
        return await self._run(_snapshot_request("ohlc", symbol, expiration, strike, right))

    async def option_snapshot_open_interest(
        self, symbol: str, expiration: DateLike, *, strike: StrikeLike = "*", right: str = "both"
    ) -> list[Row]:
        """See :meth:`ThetaTerminal.option_snapshot_open_interest`."""
        return await self._run(
            _snapshot_request("open_interest", symbol, expiration, strike, right)
        )

    async def option_history_quote(
        self,
        symbol: str,
        expiration: DateLike,
        *,
        interval: IntervalLike,
        date: DateLike | None = None,
        start_date: DateLike | None = None,
        end_date: DateLike | None = None,
        strike: StrikeLike = "*",
        right: str = "both",
        start_time: TimeLike | None = None,
        end_time: TimeLike | None = None,
    ) -> list[Row]:
        """See :meth:`ThetaTerminal.option_history_quote`."""
        return await self._run(
            _option_history_request(
                "quote",
                symbol,
                expiration,
                interval=interval,
                date=date,
                start_date=start_date,
                end_date=end_date,
                strike=strike,
                right=right,
                start_time=start_time,
                end_time=end_time,
            )
        )

    async def option_history_ohlc(
        self,
        symbol: str,
        expiration: DateLike,
        *,
        interval: IntervalLike,
        date: DateLike | None = None,
        start_date: DateLike | None = None,
        end_date: DateLike | None = None,
        strike: StrikeLike = "*",
        right: str = "both",
        start_time: TimeLike | None = None,
        end_time: TimeLike | None = None,
    ) -> list[Row]:
        """See :meth:`ThetaTerminal.option_history_ohlc`."""
        return await self._run(
            _option_history_request(
                "ohlc",
                symbol,
                expiration,
                interval=interval,
                date=date,
                start_date=start_date,
                end_date=end_date,
                strike=strike,
                right=right,
                start_time=start_time,
                end_time=end_time,
            )
        )

    async def option_history_greeks(
        self,
        symbol: str,
        expiration: DateLike,
        *,
        interval: IntervalLike,
        date: DateLike | None = None,
        start_date: DateLike | None = None,
        end_date: DateLike | None = None,
        strike: StrikeLike = "*",
        right: str = "both",
        start_time: TimeLike | None = None,
        end_time: TimeLike | None = None,
    ) -> list[Row]:
        """See :meth:`ThetaTerminal.option_history_greeks`."""
        return await self._run(
            _option_history_request(
                "greeks/first_order",
                symbol,
                expiration,
                interval=interval,
                date=date,
                start_date=start_date,
                end_date=end_date,
                strike=strike,
                right=right,
                start_time=start_time,
                end_time=end_time,
            )
        )

    async def stock_history_ohlc(
        self,
        symbol: str,
        *,
        interval: IntervalLike,
        date: DateLike | None = None,
        start_date: DateLike | None = None,
        end_date: DateLike | None = None,
        start_time: TimeLike | None = None,
        end_time: TimeLike | None = None,
    ) -> list[Row]:
        """See :meth:`ThetaTerminal.stock_history_ohlc`."""
        return await self._run(
            _stock_history_request(
                symbol,
                interval=interval,
                date=date,
                start_date=start_date,
                end_date=end_date,
                start_time=start_time,
                end_time=end_time,
            )
        )

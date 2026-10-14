# theta-terminal-client

[![CI](https://github.com/kalebhoffer/theta-terminal-client/actions/workflows/ci.yml/badge.svg)](https://github.com/kalebhoffer/theta-terminal-client/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue)

A small, typed Python client for the **Theta Terminal v3** local REST API, the HTTP
server the Theta Terminal runs on port 25503 once it has logged in to your
[Theta Data](https://www.thetadata.net) subscription.

The REST API is simple on paper but has behaviors the documentation does not cover:
CSV by default, three different response shapes, a non-standard "no data" status,
and spurious HTTP 500s on reused connections. This client handles all of them, so
the code that uses it can work with plain rows of data.

- **One dependency** (`httpx`). pandas is optional.
- **Sync and async** clients with the same methods.
- **Fully typed**, checked with `mypy --strict`.
- **Tested** against recorded response shapes, with no live Terminal needed.

> Not affiliated with or endorsed by Theta Data. If you want to call Theta Data's
> cloud directly over gRPC instead of through a local Terminal, use their official
> [`thetadata`](https://pypi.org/project/thetadata/) package.

## Install

```bash
pip install git+https://github.com/kalebhoffer/theta-terminal-client
# with DataFrame support
pip install "theta-terminal-client[pandas] @ git+https://github.com/kalebhoffer/theta-terminal-client"
```

You also need a running Theta Terminal v3 and a Theta Data subscription.

## Quick start

```python
from datetime import date, time, timedelta
from theta_terminal import ThetaTerminal, to_dataframe

with ThetaTerminal() as theta:  # defaults to http://127.0.0.1:25503
    # Live expirations only; the Terminal returns every one since 2012.
    expirations = theta.option_expirations("SPY", on_or_after=date.today())
    front = expirations[0]

    # The whole chain: Greeks, IV, bid/ask and underlying price per contract.
    chain = theta.option_snapshot_greeks("SPY", front)

    # One contract's minute-by-minute NBBO for a past session.
    quotes = theta.option_history_quote(
        "SPY",
        date(2026, 5, 15),
        strike=500,
        right="call",
        date=date(2026, 5, 1),
        interval=timedelta(minutes=1),
        start_time=time(9, 30),
        end_time=time(16, 0),
    )

df = to_dataframe(quotes)
```

Every method returns a list of flat `dict` rows. For option endpoints, each row
carries its contract's `symbol`, `expiration`, `strike` and `right` alongside the
data fields, so rows from a whole-chain request can be told apart.

### Async

```python
import asyncio
from theta_terminal import AsyncThetaTerminal


async def main() -> None:
    async with AsyncThetaTerminal(max_concurrency=4) as theta:
        expirations = await theta.option_expirations("QQQ", on_or_after="2026-05-01")
        chains = await asyncio.gather(
            *(theta.option_snapshot_greeks("QQQ", exp) for exp in expirations[:4])
        )


asyncio.run(main())
```

`max_concurrency` caps how many requests are in flight at once. The Terminal starts
failing when it is hit with many simultaneous requests, so the default is 4.

## What it handles for you

| Terminal behavior | What the client does |
|---|---|
| Responses are CSV unless you ask otherwise | Sends `format=json` on every request |
| Responses arrive as a bare list, as `{"response": [...]}`, or nested as `{"response": [{"contract": {...}, "data": [...]}]}` | Flattens all three into one list of rows, merging contract fields into each row |
| HTTP **472** means "no data found" (an expired contract, a holiday) | Returns `[]` instead of raising |
| Reused keep-alive connections cause `HTTP 500: Connection pool shut down` | Opens a fresh connection for every request |
| The same 500 is returned when your subscription tier doesn't include an endpoint (for example, historical stock data on the free tier) | Raises `ConnectionPoolShutDown` with an explanation, after retrying in case it was load |
| `option/list/expirations` returns every expiration since 2012, one row per option root (`AAPL`, `AAPL1`, `AAPLW`...) | `option_expirations()` returns unique, sorted `date` objects, with an `on_or_after` filter |
| Dates must be `YYYYMMDD`, strikes in dollars with three decimals, rights spelled `call`/`put` | Accepts `date` objects, `"2026-05-01"`, `450`, `"C"`, `timedelta(minutes=5)` and converts them |
| An HTML error page or CSV where JSON was expected | Raises `InvalidResponse` instead of a confusing decode error |

## API

Both `ThetaTerminal` and `AsyncThetaTerminal` provide:

| Method | Endpoint |
|---|---|
| `option_expirations(symbol, *, on_or_after=None)` | `/v3/option/list/expirations` |
| `option_snapshot_quote(symbol, expiration, *, strike="*", right="both")` | `/v3/option/snapshot/quote` |
| `option_snapshot_greeks(...)` | `/v3/option/snapshot/greeks/first_order` |
| `option_snapshot_ohlc(...)` | `/v3/option/snapshot/ohlc` |
| `option_snapshot_open_interest(...)` | `/v3/option/snapshot/open_interest` |
| `option_history_quote(symbol, expiration, *, interval, date=..., strike="*", right="both", start_time=None, end_time=None)` | `/v3/option/history/quote` |
| `option_history_ohlc(...)` | `/v3/option/history/ohlc` |
| `option_history_greeks(...)` | `/v3/option/history/greeks/first_order` |
| `stock_history_ohlc(symbol, *, interval, date=..., start_time=None, end_time=None)` | `/v3/stock/history/ohlc` |
| `get(path, **params)` | Any other v3 endpoint, with the same parsing and error handling |

History methods take either `date=` for one session or `start_date=` and
`end_date=` for a range.

`strike="*"` and `right="both"` request the whole chain. Strikes are in dollars:
pass `500` or `"452.5"`, not the older `500000` encoding.

`option_history_greeks` rows include `underlying_price`, which makes it a way to
get intraday underlying prices on subscription tiers that don't include historical
stock data.

### Client options

```python
ThetaTerminal(
    base_url="http://127.0.0.1:25503",
    timeout=30.0,  # seconds per request
    max_retries=2,  # extra attempts after a connection failure or HTTP 5xx
    retry_backoff=0.5,  # seconds before the first retry, doubled each time
)
```

Read timeouts are not retried. A slow response usually means a large request, and
sending it again only adds load.

### Errors

All errors derive from `ThetaTerminalError`:

- `TerminalUnavailable`: the Terminal could not be reached. It may not be running,
  may still be logging in, or may be on a different host or port.
- `TerminalHTTPError`: an HTTP error status, with `.status_code`, `.body` and `.url`.
  - `ConnectionPoolShutDown`: the misleading 500 described above.
- `InvalidResponse`: the body was empty or was not JSON.

## Running the Terminal

Theta Terminal v3 is a Java 21 program. Download `ThetaTerminalv3.jar` from Theta
Data, put your login in `creds.txt` (email on the first line, password on the
second) and run:

```bash
java -jar ThetaTerminalv3.jar --creds-file creds.txt
```

When it has logged in, the REST API is at `http://127.0.0.1:25503`. v3 only serves
this port and a WebSocket on 25520; the v2 ports (25510, 11000, 10000) are gone.

**In Docker Compose,** next to your app (the Terminal needs about 1 GB of memory):

```yaml
services:
  theta-terminal:
    image: eclipse-temurin:21-jre
    working_dir: /opt/theta-terminal
    volumes:
      - ./theta-terminal:/opt/theta-terminal   # holds ThetaTerminalv3.jar
    environment:
      THETADATA_EMAIL: ${THETADATA_EMAIL}
      THETADATA_PASSWORD: ${THETADATA_PASSWORD}
    command: >
      bash -c 'printf "%s\n%s\n" "$$THETADATA_EMAIL" "$$THETADATA_PASSWORD" > creds.txt
      && chmod 600 creds.txt
      && exec java -Xmx768m -jar ThetaTerminalv3.jar --creds-file creds.txt'
    expose: ["25503"]   # internal network only; don't publish it
    restart: unless-stopped
    deploy:
      resources:
        limits:
          memory: 1G
```

Then point the client at the service name:
`ThetaTerminal("http://theta-terminal:25503")`.

**Tips from running it in production:**

- If requests that used to work start returning 500s mid-session, restart the
  Terminal.
- In loops that request the same contract repeatedly, cache failed lookups as well
  as successful ones, so a missing contract isn't requested hundreds of times.

## Development

```bash
uv sync
uv run pytest
uv run ruff check .
uv run mypy
```

Bug reports are welcome, especially new response shapes or status codes the
Terminal produces. Please include the endpoint, parameters and a redacted
response body.

## License

[MIT](LICENSE)

# Changelog

## 0.1.0 (2026-10-14)

First release.

- Sync (`ThetaTerminal`) and async (`AsyncThetaTerminal`) clients for the Theta
  Terminal v3 REST API.
- Methods for option expirations, snapshots (quote, Greeks, OHLC, open interest),
  option history (quote, OHLC, Greeks) and stock history OHLC, plus `get()` for any
  other endpoint.
- Handles the Terminal's undocumented behavior: CSV by default, three response
  shapes, HTTP 472 for "no data", and spurious 500s on reused connections.
- Retries with exponential backoff, a concurrency cap for the async client, and
  typed errors.
- Optional `to_dataframe()` helper for pandas.

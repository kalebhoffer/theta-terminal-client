"""Turn Terminal v3 response bodies into flat lists of rows."""

from __future__ import annotations

import json
from typing import Any

from .errors import InvalidResponse

Row = dict[str, Any]


def decode_json(body: bytes, content_type: str, url: str) -> Any:
    """Decode a response body, failing clearly on anything that is not JSON."""
    if not body.strip():
        raise InvalidResponse(f"empty response body from {url}")
    looks_like_json = body.lstrip()[:1] in (b"{", b"[")
    if "json" not in content_type.lower() and not looks_like_json:
        raise InvalidResponse(
            f"expected JSON from {url} but got content-type {content_type!r}; "
            "is a proxy in front of the Terminal, or is it a pre-v3 Terminal?"
        )
    try:
        return json.loads(body)
    except ValueError as exc:
        raise InvalidResponse(f"malformed JSON from {url}: {exc}") from None


def flatten(payload: Any) -> list[Row]:
    """Normalize every response shape the Terminal produces into flat rows.

    Terminal v3 has been seen to return:

    * a bare list of row objects (what the docs describe);
    * ``{"response": [row, ...]}``;
    * ``{"response": [{"contract": {...}, "data": [row, ...]}, ...]}`` for
      option snapshot and history endpoints.

    Nested contract fields (symbol, expiration, strike, right) are merged
    into each data row, so every returned row is self-describing. Entries
    that are not objects are skipped.
    """
    if isinstance(payload, dict):
        payload = payload.get("response", [])
    if not isinstance(payload, list):
        return []

    rows: list[Row] = []
    for entry in payload:
        if not isinstance(entry, dict):
            continue
        data = entry.get("data")
        if isinstance(data, list):
            contract = entry.get("contract") or {}
            if not isinstance(contract, dict):
                contract = {}
            for tick in data:
                if isinstance(tick, dict):
                    rows.append({**contract, **tick})
        else:
            rows.append(entry)
    return rows

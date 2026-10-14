"""A small, dependable client for the Theta Terminal v3 local REST API."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ._parse import Row
from .client import DEFAULT_BASE_URL, AsyncThetaTerminal, ThetaTerminal
from .errors import (
    ConnectionPoolShutDown,
    InvalidResponse,
    TerminalHTTPError,
    TerminalUnavailable,
    ThetaTerminalError,
)

if TYPE_CHECKING:
    import pandas as pd

__version__ = "0.1.0"

__all__ = [
    "DEFAULT_BASE_URL",
    "AsyncThetaTerminal",
    "ConnectionPoolShutDown",
    "InvalidResponse",
    "Row",
    "TerminalHTTPError",
    "TerminalUnavailable",
    "ThetaTerminal",
    "ThetaTerminalError",
    "to_dataframe",
]


def to_dataframe(rows: list[Row]) -> pd.DataFrame:
    """Turn rows from any client method into a pandas DataFrame.

    Requires the ``pandas`` extra: ``pip install "theta-terminal-client[pandas]"``.
    """
    try:
        import pandas as pd
    except ImportError:
        raise ImportError(
            'to_dataframe needs pandas: pip install "theta-terminal-client[pandas]"'
        ) from None
    return pd.DataFrame(rows)

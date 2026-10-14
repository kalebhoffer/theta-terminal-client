"""Exceptions raised by the client.

Everything derives from :class:`ThetaTerminalError`, so callers that only care
whether a request worked can catch that one class.
"""

from __future__ import annotations


class ThetaTerminalError(Exception):
    """Base class for every error this library raises."""


class TerminalUnavailable(ThetaTerminalError):
    """The Terminal could not be reached (refused, reset, or timed out).

    Usually the Terminal is not running, is still logging in, or is listening
    on a different host or port than the client was given.
    """


class InvalidResponse(ThetaTerminalError):
    """The Terminal answered, but not with the JSON this client expects.

    Typical causes are an empty body, an HTML error page from a proxy in
    front of the Terminal, or CSV from an old Terminal that ignored
    ``format=json``.
    """


class TerminalHTTPError(ThetaTerminalError):
    """The Terminal returned an HTTP error status.

    HTTP 472 ("no data found") is never raised; the client treats it as an
    empty result.
    """

    def __init__(self, status_code: int, body: str, url: str) -> None:
        self.status_code = status_code
        self.body = body
        self.url = url
        snippet = body.strip().replace("\n", " ")[:200]
        super().__init__(f"HTTP {status_code} from {url}: {snippet}")


class ConnectionPoolShutDown(TerminalHTTPError):
    """HTTP 500 with the body ``Connection pool shut down``.

    Despite the wording, this almost never means the Terminal is down. In
    practice it has two causes:

    * **Your subscription tier does not include the endpoint.** For example,
      the free stock tier answers every historical stock endpoint this way.
      Retrying will not help.
    * **The Terminal is overloaded** by rapid or keep-alive requests. This
      client already disables keep-alive; if you still see it, slow down or
      lower ``max_concurrency``, and restart the Terminal if it persists.
    """

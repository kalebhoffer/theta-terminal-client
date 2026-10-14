from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import httpx
import pytest

from theta_terminal import AsyncThetaTerminal, ThetaTerminal

Responder = Callable[[httpx.Request], httpx.Response]


def json_response(payload: Any, status: int = 200) -> httpx.Response:
    return httpx.Response(
        status, content=json.dumps(payload), headers={"content-type": "application/json"}
    )


class FakeTerminal:
    """Records requests and answers them from a queue of responders."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.responses: list[Responder | httpx.Response] = []

    def queue(self, *responses: Responder | httpx.Response) -> None:
        self.responses.extend(responses)

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if not self.responses:
            return json_response({"response": []})
        nxt = self.responses.pop(0)
        return nxt(request) if callable(nxt) else nxt

    @property
    def last_params(self) -> dict[str, str]:
        return dict(self.requests[-1].url.params)


@pytest.fixture
def terminal() -> FakeTerminal:
    return FakeTerminal()


@pytest.fixture
def client(terminal: FakeTerminal) -> ThetaTerminal:
    return ThetaTerminal(retry_backoff=0, transport=httpx.MockTransport(terminal.handle))


@pytest.fixture
def aclient(terminal: FakeTerminal) -> AsyncThetaTerminal:
    return AsyncThetaTerminal(retry_backoff=0, transport=httpx.MockTransport(terminal.handle))

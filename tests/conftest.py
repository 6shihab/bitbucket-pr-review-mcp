"""Shared fixtures.

Every test builds a real BitbucketClient over a mock transport, so everything above the
wire — the allowlist guard, the write chokepoint, path normalisation, response handling
— is the production code. The seam is the transport and nothing else (ADR-0005's
testing note; agreed before any of this was written).
"""

from __future__ import annotations

import httpx
import pytest

from bitbucket_pr_review_mcp.client import BitbucketClient
from bitbucket_pr_review_mcp.credentials import Credential
from bitbucket_pr_review_mcp.references import Repository
from bitbucket_pr_review_mcp.settings import Allowlist

ALLOWED = "streamstech/db-explorer"
FORBIDDEN = "streamstech/secret-payroll"


@pytest.fixture
def allowlist() -> Allowlist:
    return Allowlist.of([Repository("streamstech", "db-explorer")])


@pytest.fixture
def credential() -> Credential:
    return Credential(email="reviewer@streamstech.com", token="not-a-real-token")


class Wire:
    """A recording mock transport. `wire.requests` is what actually left the process."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self._responses: list[httpx.Response] = []

    def will_return(self, *responses: httpx.Response) -> Wire:
        self._responses.extend(responses)
        return self

    def transport(self) -> httpx.MockTransport:
        def handle(request: httpx.Request) -> httpx.Response:
            self.requests.append(request)
            if self._responses:
                return self._responses.pop(0)
            return httpx.Response(200, json={})

        return httpx.MockTransport(handle)

    @property
    def called(self) -> bool:
        return bool(self.requests)

    @property
    def last(self) -> httpx.Request:
        assert self.requests, "nothing reached the transport"
        return self.requests[-1]


@pytest.fixture
def wire() -> Wire:
    return Wire()


@pytest.fixture
def client(wire: Wire, allowlist: Allowlist, credential: Credential):
    http = httpx.AsyncClient(transport=wire.transport(), base_url="https://api.bitbucket.org")
    return BitbucketClient(http=http, allowlist=allowlist, credential=credential)

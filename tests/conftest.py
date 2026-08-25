"""Shared fixtures.

Every test builds a real BitbucketClient over a mock transport, so everything above the
wire — the allowlist guard, the write chokepoint, path normalisation, response handling
— is the production code. The seam is the transport and nothing else (ADR-0005's
testing note; agreed before any of this was written).
"""

from __future__ import annotations

from datetime import date

import httpx
import keyring
import pytest

from bitbucket_pr_review_mcp.client import BitbucketClient
from bitbucket_pr_review_mcp.credentials import Credential, StoredCredential
from bitbucket_pr_review_mcp.gate import CredentialGate
from bitbucket_pr_review_mcp.keychain import Keychain
from bitbucket_pr_review_mcp.references import Repository
from bitbucket_pr_review_mcp.settings import Allowlist, Settings

ALLOWED = "streamstech/db-explorer"
FORBIDDEN = "streamstech/secret-payroll"


@pytest.fixture(autouse=True)
def _no_dotenv(monkeypatch):
    """Settings reads `.env`, and a deployment has a real one. Tests must not.

    Found when the shared server grew a filled-in `.env`: a test that unset
    `BB_MCP_OIDC_CLIENT_SECRET` still saw a secret, because deleting the variable
    does not touch the file underneath it. The suite had been passing on the
    accident that nobody's `.env` held anything it asked about.
    """
    monkeypatch.setitem(Settings.model_config, "env_file", None)


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


@pytest.fixture
def memory_keyring():
    """Install a real keyring backend that keeps secrets in a dict, for one test."""
    from .memory_keyring import InMemoryKeyring

    previous = keyring.get_keyring()
    backend = InMemoryKeyring()
    keyring.set_keyring(backend)
    yield backend
    keyring.set_keyring(previous)


@pytest.fixture
def keychain(memory_keyring) -> Keychain:
    return Keychain()


class RecordingSetup:
    """The setup listener, reduced to the only two things the gate can do to it."""

    URL = "http://127.0.0.1:54321/setup?token=one-time"

    def __init__(self) -> None:
        self.starts = 0
        self.stops = 0
        self.on_saved = None

    def start(self, on_saved) -> str:
        self.starts += 1
        self.on_saved = on_saved
        return self.URL

    def stop(self) -> None:
        self.stops += 1


@pytest.fixture
def setup_listener() -> RecordingSetup:
    return RecordingSetup()


@pytest.fixture
def empty_gate(keychain, setup_listener) -> CredentialGate:
    """A gate with nothing in the keychain: every tool should point at setup."""
    return CredentialGate(keychain, setup_listener)


@pytest.fixture
def gate(empty_gate, keychain, credential) -> CredentialGate:
    """A gate holding a credential that is good for years."""
    keychain.save(StoredCredential(credential=credential, expires_on=date(2099, 1, 1)))
    return empty_gate

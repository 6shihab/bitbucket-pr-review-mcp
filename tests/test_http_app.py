"""The shared server over HTTP: who gets in, what they are told, and whose call it is.

The app is driven in process over an ASGI transport, so the routing, the middleware and
FastMCP's own request handling are all production code. Two seams and no more: the
authorization server's endpoints, and Bitbucket's.
"""

from __future__ import annotations

import json
from datetime import date

import httpx
import pytest
from asgi_lifespan import LifespanManager

from bitbucket_pr_review_mcp.credentials import Credential, StoredCredential
from bitbucket_pr_review_mcp.discovery import REVIEW_SCOPE, ProtectedResource
from bitbucket_pr_review_mcp.http_app import build_http_app
from bitbucket_pr_review_mcp.settings import Settings
from bitbucket_pr_review_mcp.tokens import SigningKeys, TokenVerifier
from bitbucket_pr_review_mcp.vault import CredentialVault, VaultKey

from . import fixtures
from .oauth import ISSUER, KID, RESOURCE, Keycloak, new_key, public_jwk, token_for

PR = "https://bitbucket.org/streamstech/db-explorer/pull-requests/42"

ALICE = "f:9c1e:alice"
BOB = "f:9c1e:bob"

MCP_HEADERS = {
    "content-type": "application/json",
    "accept": "application/json, text/event-stream",
}


@pytest.fixture(scope="module")
def signing():
    return new_key()


@pytest.fixture
def keycloak(signing) -> Keycloak:
    return Keycloak([public_jwk(signing, KID)])


@pytest.fixture
def resource() -> ProtectedResource:
    return ProtectedResource.of(RESOURCE, ISSUER)


@pytest.fixture
def vault(tmp_path):
    store = CredentialVault.at(tmp_path / "credentials.sqlite3", VaultKey.generate())
    yield store
    store.close()


@pytest.fixture
def app(keycloak, resource, vault, allowlist, setup_listener, wire):
    verifier = TokenVerifier(resource=resource, keys=SigningKeys(ISSUER, keycloak.client()))
    return build_http_app(
        Settings(),
        allowlist,
        vault,
        verifier,
        resource,
        setup_listener,
        http_factory=lambda: httpx.AsyncClient(
            transport=wire.transport(), base_url="https://api.bitbucket.org"
        ),
    )


@pytest.fixture
async def caller(app):
    """`LifespanManager` rather than the app's own lifespan context: FastMCP's session
    manager opens an anyio cancel scope, and pytest-asyncio finalises a generator fixture
    in a different task than it started it, which anyio refuses."""
    async with LifespanManager(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="https://review.streamstech.com"
        ) as client:
            yield client


def person_from(subject: str, signing) -> str:
    """The vault key the middleware will resolve this subject to."""
    import hashlib

    return hashlib.sha256(f"{ISSUER}\x00{subject}".encode()).hexdigest()


def connected(vault, person: str, email: str) -> None:
    vault.save(
        person,
        StoredCredential(
            credential=Credential(email=email, token=f"token-of-{email}"),
            expires_on=date(2099, 1, 1),
        ),
    )


async def call(caller: httpx.AsyncClient, token: str | None, method: str, **params):
    headers = dict(MCP_HEADERS)
    if token:
        headers["authorization"] = f"Bearer {token}"
    body = {"jsonrpc": "2.0", "id": 1, "method": method}
    if params:
        body["params"] = params
    return await caller.post("/mcp", headers=headers, content=json.dumps(body))


def payload(response: httpx.Response) -> dict:
    """Streamable HTTP answers as JSON or as one SSE event, depending on the request."""
    if response.headers.get("content-type", "").startswith("text/event-stream"):
        for line in response.text.splitlines():
            if line.startswith("data: "):
                return json.loads(line.removeprefix("data: "))
    return response.json()


class TestTheMetadataDocument:
    async def test_it_is_served_without_a_token(self, caller, resource):
        response = await caller.get(resource.metadata_path())

        assert response.status_code == 200
        assert response.json() == resource.document()

    async def test_it_is_served_at_the_bare_well_known_path_too(self, caller):
        """Claude probes the path-suffixed form first and falls back to this one."""
        response = await caller.get("/.well-known/oauth-protected-resource")

        assert response.status_code == 200

    async def test_it_names_the_authorization_server(self, caller, resource):
        document = (await caller.get(resource.metadata_path())).json()

        assert document["authorization_servers"] == [ISSUER]


class TestWhatAnUnauthenticatedCallerIsTold:
    async def test_it_is_a_401_and_not_a_polite_refusal(self, caller):
        """Claude does not read `WWW-Authenticate` off a 200, so a tool-level error here
        means the connector never discovers where to send anybody."""
        response = await call(caller, None, "tools/list")

        assert response.status_code == 401

    async def test_it_points_at_the_metadata(self, caller, resource):
        response = await call(caller, None, "tools/list")

        assert f'resource_metadata="{resource.metadata_url()}"' in (
            response.headers["www-authenticate"]
        )

    async def test_it_names_the_scope_that_is_wanted(self, caller):
        response = await call(caller, None, "tools/list")

        assert REVIEW_SCOPE in response.headers["www-authenticate"]

    async def test_rubbish_is_refused_the_same_way(self, caller):
        response = await call(caller, "not-a-token", "tools/list")

        assert response.status_code == 401
        assert "www-authenticate" in response.headers

    async def test_a_token_for_another_resource_does_not_get_in(self, caller, signing):
        elsewhere = token_for(signing, audience="https://something-else.example")

        assert (await call(caller, elsewhere, "tools/list")).status_code == 401

    async def test_too_few_scopes_is_a_403_that_says_so(self, caller, signing):
        thin = token_for(signing, scope="openid")

        response = await call(caller, thin, "tools/list")

        assert response.status_code == 403
        assert 'error="insufficient_scope"' in response.headers["www-authenticate"]

    async def test_an_unreachable_issuer_is_not_the_callers_fault(
        self, caller, signing, keycloak
    ):
        """A 401 here would send a client round the authorisation loop for a problem no
        amount of reauthorising can fix."""
        keycloak.unreachable = True

        response = await call(caller, token_for(signing), "tools/list")

        assert response.status_code == 503
        assert "www-authenticate" not in response.headers

    async def test_no_refusal_carries_the_token(self, caller, signing):
        expired = token_for(signing, scope="openid")

        response = await call(caller, expired, "tools/list")

        assert expired not in response.text
        assert expired not in str(response.headers)


class TestAnAuthenticatedCaller:
    async def test_the_tools_are_there(self, caller, signing):
        response = await call(caller, token_for(signing), "tools/list")

        assert response.status_code == 200
        names = [tool["name"] for tool in payload(response)["result"]["tools"]]
        assert "bitbucket_get_pull_request" in names

    async def test_there_are_still_eleven_of_them(self, caller, signing):
        """The shared server is a deployment choice, not a different product."""
        response = await call(caller, token_for(signing), "tools/list")

        assert len(payload(response)["result"]["tools"]) == 11

    async def test_somebody_who_has_not_connected_bitbucket_is_sent_to_setup(
        self, caller, signing, setup_listener
    ):
        response = await call(
            caller,
            token_for(signing),
            "tools/call",
            name="bitbucket_get_pull_request",
            arguments={"pull_request": PR},
        )

        assert setup_listener.URL in json.dumps(payload(response))


class TestWhoseCallItIs:
    """The sentence the whole shared deployment exists to be able to say."""

    async def test_a_tool_uses_the_credential_of_the_person_who_called(
        self, caller, signing, vault, wire
    ):
        connected(vault, person_from(ALICE, signing), "alice@streamstech.com")
        wire.will_return(httpx.Response(200, json=fixtures.pull_request()))

        await call(
            caller,
            token_for(signing, sub=ALICE),
            "tools/call",
            name="bitbucket_get_pull_request",
            arguments={"pull_request": PR},
        )

        assert _basic_auth(wire.last).startswith("alice@streamstech.com:")

    async def test_two_people_do_not_get_each_others(
        self, caller, signing, vault, wire
    ):
        connected(vault, person_from(ALICE, signing), "alice@streamstech.com")
        connected(vault, person_from(BOB, signing), "bob@streamstech.com")
        wire.will_return(
            httpx.Response(200, json=fixtures.pull_request()),
            httpx.Response(200, json=fixtures.pull_request()),
        )

        for subject in (ALICE, BOB):
            await call(
                caller,
                token_for(signing, sub=subject),
                "tools/call",
                name="bitbucket_get_pull_request",
                arguments={"pull_request": PR},
            )

        used = [_basic_auth(request).split(":")[0] for request in wire.requests]
        assert used == ["alice@streamstech.com", "bob@streamstech.com"]

    async def test_bobs_call_does_not_reuse_alices_session(
        self, caller, signing, vault, wire
    ):
        """Alice connects, Bob does not. Bob must be asked to connect rather than handed
        whatever the last caller left behind."""
        connected(vault, person_from(ALICE, signing), "alice@streamstech.com")
        wire.will_return(httpx.Response(200, json=fixtures.pull_request()))
        await call(
            caller,
            token_for(signing, sub=ALICE),
            "tools/call",
            name="bitbucket_get_pull_request",
            arguments={"pull_request": PR},
        )

        response = await call(
            caller,
            token_for(signing, sub=BOB),
            "tools/call",
            name="bitbucket_get_pull_request",
            arguments={"pull_request": PR},
        )

        assert "alice@streamstech.com" not in json.dumps(payload(response))
        assert len(wire.requests) == 1, "Bob's call must not have reached Bitbucket"

    async def test_nothing_in_a_tool_argument_can_change_whose_call_it_is(
        self, caller, signing, vault, wire
    ):
        """A pull request description asking to be reviewed 'as the administrator' is
        describing something the transport makes unsayable. The attempt does not fail a
        permission check — there is no parameter to carry it, so it is refused as
        malformed, and nothing reaches Bitbucket."""
        connected(vault, person_from(ALICE, signing), "alice@streamstech.com")
        connected(vault, person_from(BOB, signing), "bob@streamstech.com")

        response = await call(
            caller,
            token_for(signing, sub=ALICE),
            "tools/call",
            name="bitbucket_get_pull_request",
            arguments={"pull_request": PR, "person": person_from(BOB, signing)},
        )

        assert payload(response)["result"]["isError"] is True
        assert not wire.called

    async def test_the_person_is_not_a_parameter_of_any_tool(self, caller, signing):
        response = await call(caller, token_for(signing), "tools/list")

        for tool in payload(response)["result"]["tools"]:
            for name in tool["inputSchema"].get("properties", {}):
                assert not any(
                    word in name.lower()
                    for word in ("person", "subject", "user", "account", "as_")
                ), f"{tool['name']} takes {name}"


def _basic_auth(request: httpx.Request) -> str:
    import base64

    encoded = request.headers["authorization"].removeprefix("Basic ")
    return base64.b64decode(encoded).decode()

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
from starlette.applications import Starlette

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


class Connected:
    """Stands in for the connect page, and captures the callback it is handed.

    That callback is the whole point of ticket 14: the page writes to the vault, and
    unless it can say so, every session that already read a credential goes on using it.
    """

    def __init__(self) -> None:
        self.forget = None

    def factory(self, forget):
        self.forget = forget
        return Starlette(routes=[])


@pytest.fixture
def connect_page() -> Connected:
    return Connected()


@pytest.fixture
def app(keycloak, resource, vault, allowlist, setup_listener, wire, connect_page):
    verifier = TokenVerifier(resource=resource, keys=SigningKeys(ISSUER, keycloak.client()))
    return build_http_app(
        Settings(),
        allowlist,
        vault,
        verifier,
        resource,
        setup_listener,
        connect_factory=connect_page.factory,
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


class TestWhenSomebodyChangesTheirCredential:
    """The connect page writes straight to the vault. A session that has already read a
    credential must be told, or it keeps using one that has been replaced or removed."""

    async def test_disconnecting_stops_the_next_tool_call(
        self, caller, signing, vault, wire, connect_page
    ):
        person = person_from(ALICE, signing)
        connected(vault, person, "alice@streamstech.com")
        wire.will_return(httpx.Response(200, json=fixtures.pull_request()))
        await call(
            caller,
            token_for(signing, sub=ALICE),
            "tools/call",
            name="bitbucket_get_pull_request",
            arguments={"pull_request": PR},
        )
        assert len(wire.requests) == 1

        vault.clear(person)
        connect_page.forget(person)

        response = await call(
            caller,
            token_for(signing, sub=ALICE),
            "tools/call",
            name="bitbucket_get_pull_request",
            arguments={"pull_request": PR},
        )

        assert len(wire.requests) == 1, "the removed credential must not be reused"
        assert "connect" in json.dumps(payload(response)).lower()

    async def test_reconnecting_a_different_account_changes_who_posts(
        self, caller, signing, vault, wire, connect_page
    ):
        """Otherwise the comment says one name and the account behind it is another —
        which is the kind of wrong that survives review because it looks fine."""
        person = person_from(ALICE, signing)
        connected(vault, person, "alice@streamstech.com")
        wire.will_return(
            httpx.Response(200, json=fixtures.pull_request()),
            httpx.Response(200, json=fixtures.pull_request()),
        )
        await call(
            caller,
            token_for(signing, sub=ALICE),
            "tools/call",
            name="bitbucket_get_pull_request",
            arguments={"pull_request": PR},
        )

        connected(vault, person, "alice.other@streamstech.com")
        connect_page.forget(person)
        await call(
            caller,
            token_for(signing, sub=ALICE),
            "tools/call",
            name="bitbucket_get_pull_request",
            arguments={"pull_request": PR},
        )

        assert _basic_auth(wire.last).startswith("alice.other@streamstech.com:")

    async def test_forgetting_one_person_leaves_everybody_else_connected(
        self, caller, signing, vault, wire, connect_page
    ):
        connected(vault, person_from(ALICE, signing), "alice@streamstech.com")
        connected(vault, person_from(BOB, signing), "bob@streamstech.com")
        wire.will_return(*[httpx.Response(200, json=fixtures.pull_request())] * 3)
        for subject in (ALICE, BOB):
            await call(
                caller,
                token_for(signing, sub=subject),
                "tools/call",
                name="bitbucket_get_pull_request",
                arguments={"pull_request": PR},
            )

        connect_page.forget(person_from(ALICE, signing))

        await call(
            caller,
            token_for(signing, sub=BOB),
            "tools/call",
            name="bitbucket_get_pull_request",
            arguments={"pull_request": PR},
        )

        assert _basic_auth(wire.last).startswith("bob@streamstech.com:")


class TestTwoPeopleAtOnce:
    async def test_concurrent_calls_use_their_own_credentials(
        self, caller, signing, vault, wire
    ):
        """Not interleaved by luck: both requests are in flight together."""
        import asyncio

        connected(vault, person_from(ALICE, signing), "alice@streamstech.com")
        connected(vault, person_from(BOB, signing), "bob@streamstech.com")
        wire.will_return(*[httpx.Response(200, json=fixtures.pull_request())] * 20)

        async def review(subject: str):
            return await call(
                caller,
                token_for(signing, sub=subject),
                "tools/call",
                name="bitbucket_get_pull_request",
                arguments={"pull_request": PR},
            )

        await asyncio.gather(*[review(who) for who in (ALICE, BOB) for _ in range(5)])

        used = sorted({_basic_auth(request).split(":")[0] for request in wire.requests})
        assert used == ["alice@streamstech.com", "bob@streamstech.com"]
        assert len(wire.requests) == 10

    async def test_nobody_is_served_with_an_unauthenticated_session(self, caller, signing):
        """`PerPerson` refuses rather than guessing, if a tool ever runs unbound."""
        from bitbucket_pr_review_mcp.sessions import NoCaller, PerPerson

        with pytest.raises(NoCaller):
            PerPerson(lambda person: None).current()


class Bitbucket:
    """A Bitbucket that routes rather than queues.

    A queue of responses assumes every call makes the same requests in the same order,
    and the shared diff cache means the second person's review does not re-fetch the
    diff. Routing also lets the answer depend on *which credential asked*, which is the
    thing these tests are actually about.
    """

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path, email = request.url.path, _basic_auth(request).split(":")[0]

        if path.endswith("/2.0/user"):
            return httpx.Response(
                200,
                json={
                    "type": "user",
                    "display_name": email.split("@")[0].title() + " Example",
                    "account_id": f"account-of-{email}",
                    "uuid": "{b4d2e8f1-3c5a-4e7b-9d1f-2a6c8e0b4d7f}",
                },
            )
        if path.endswith("/diff"):
            return httpx.Response(200, text=fixtures.UNIFIED_DIFF)
        if path.endswith("/comments"):
            if request.method == "POST":
                return httpx.Response(
                    201, json={"id": 3001, "inline": {"path": "src/app/retry.py", "to": 14}}
                )
            return httpx.Response(200, json={"values": []})
        return httpx.Response(200, json=fixtures.pull_request())

    def posted(self) -> list[httpx.Request]:
        return [r for r in self.requests if r.method == "POST"]

    def asked_who_they_are(self) -> list[str]:
        return [
            _basic_auth(r).split(":")[0]
            for r in self.requests
            if r.url.path.endswith("/2.0/user")
        ]


class TestTheAttributionFooter:
    """Ticket 05 put a name on every comment so somebody is accountable for it. On a
    shared server that name has to be the person who asked, not whoever this process
    happened to ask Bitbucket about first."""

    @pytest.fixture
    def bitbucket(self) -> Bitbucket:
        return Bitbucket()

    @pytest.fixture
    def app(self, keycloak, resource, vault, allowlist, setup_listener, bitbucket):
        verifier = TokenVerifier(
            resource=resource, keys=SigningKeys(ISSUER, keycloak.client())
        )
        return build_http_app(
            Settings(),
            allowlist,
            vault,
            verifier,
            resource,
            setup_listener,
            http_factory=lambda: httpx.AsyncClient(
                transport=httpx.MockTransport(bitbucket.handle),
                base_url="https://api.bitbucket.org",
            ),
        )

    @staticmethod
    async def comment_as(caller, signing, subject: str):
        return await call(
            caller,
            token_for(signing, sub=subject),
            "tools/call",
            name="bitbucket_add_pr_comment",
            arguments={
                "pull_request": PR,
                "review_basis": fixtures.BASIS,
                "comments": [
                    {
                        "severity": "HIGH",
                        "message": "`RETRIES` is undefined on this path.",
                        "path": "src/app/retry.py",
                        "line": 14,
                        "side": "added",
                    }
                ],
            },
        )

    @staticmethod
    def posted_body(bitbucket: Bitbucket) -> str:
        posted = bitbucket.posted()
        assert posted, "nothing was posted"
        return json.loads(posted[-1].content)["content"]["raw"]

    async def test_it_names_the_person_whose_credential_posted(
        self, caller, signing, vault, bitbucket
    ):
        connected(vault, person_from(ALICE, signing), "alice@streamstech.com")

        await self.comment_as(caller, signing, ALICE)

        body = self.posted_body(bitbucket)
        assert "Alice Example" in body
        assert "alice@streamstech.com" in body

    async def test_two_people_are_not_attributed_to_each_other(
        self, caller, signing, vault, bitbucket
    ):
        """The failure this prevents looks fine in review: a comment carrying one
        person's name, posted with another person's token."""
        connected(vault, person_from(ALICE, signing), "alice@streamstech.com")
        connected(vault, person_from(BOB, signing), "bob@streamstech.com")

        await self.comment_as(caller, signing, ALICE)
        alice_said = self.posted_body(bitbucket)
        alice_used = _basic_auth(bitbucket.posted()[-1]).split(":")[0]

        await self.comment_as(caller, signing, BOB)
        bob_said = self.posted_body(bitbucket)
        bob_used = _basic_auth(bitbucket.posted()[-1]).split(":")[0]

        assert "Alice Example" in alice_said and "alice@streamstech.com" in alice_said
        assert "Bob Example" in bob_said and "bob@streamstech.com" in bob_said
        assert (alice_used, bob_used) == ("alice@streamstech.com", "bob@streamstech.com")
        assert "alice" not in bob_said.lower()

    async def test_ours_means_this_persons_and_not_this_servers(
        self, caller, signing, vault, bitbucket
    ):
        """Each person asks Bitbucket who *they* are. One cached answer shared between
        them is how one person ends up editing another's summary comment."""
        connected(vault, person_from(ALICE, signing), "alice@streamstech.com")
        connected(vault, person_from(BOB, signing), "bob@streamstech.com")

        await self.comment_as(caller, signing, ALICE)
        await self.comment_as(caller, signing, BOB)

        assert bitbucket.asked_who_they_are() == [
            "alice@streamstech.com",
            "bob@streamstech.com",
        ]

    async def test_one_person_reviewing_twice_asks_who_they_are_once(
        self, caller, signing, vault, bitbucket
    ):
        """The cache ticket 05 added is still a cache; it is just per person now."""
        connected(vault, person_from(ALICE, signing), "alice@streamstech.com")

        await self.comment_as(caller, signing, ALICE)
        await self.comment_as(caller, signing, ALICE)

        assert bitbucket.asked_who_they_are() == ["alice@streamstech.com"]

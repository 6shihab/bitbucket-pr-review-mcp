"""Signing a browser in with Keycloak.

The small half of OAuth, and the half where getting it wrong is quiet: a login that skips
the `state` check lets somebody else's sign-in land in this browser, and one that skips
the audience check accepts an identity minted for a different client entirely.
"""

from __future__ import annotations

import base64
import hashlib
from datetime import UTC, datetime, timedelta

import httpx
import jwt
import pytest

from bitbucket_pr_review_mcp.oidc import Handshake, LoginFailed, RelyingParty
from bitbucket_pr_review_mcp.tokens import IssuerUnreachable, SigningKeys, person_id

from .oauth import ISSUER, KID, Keycloak, new_key, public_jwk

CLIENT = "bitbucket-pr-review-web"
REDIRECT = "https://review.streamstech.com/connect/callback"
SUBJECT = "78d77e24-0852-4bfb-9748-35345c05020e"


@pytest.fixture(scope="module")
def signing():
    return new_key()


def identity_token(private, *, kid: str = KID, **overrides) -> str:
    claims = {
        "iss": ISSUER,
        "sub": SUBJECT,
        "aud": CLIENT,
        "exp": datetime.now(UTC) + timedelta(minutes=5),
        "iat": datetime.now(UTC),
        "name": "Alice Example",
        "preferred_username": "alice",
        "email": "alice@streamstech.com",
    }
    claims.update(overrides)
    claims = {name: value for name, value in claims.items() if value is not None}
    return jwt.encode(claims, private, algorithm="RS256", headers={"kid": kid})


class Realm(Keycloak):
    """Keycloak, plus the token endpoint a login actually exchanges its code at."""

    def __init__(self, keys, identity: str) -> None:
        super().__init__(keys)
        self.identity = identity
        self.exchanges: list[dict] = []
        self.refuses = False

    def handle(self, request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/protocol/openid-connect/token"):
            self.exchanges.append(dict(httpx.QueryParams(request.content.decode())))
            if self.refuses:
                return httpx.Response(
                    400, json={"error": "invalid_grant", "code_was": "leaked-if-shown"}
                )
            return httpx.Response(200, json={"id_token": self.identity, "token_type": "Bearer"})
        return super().handle(request)


@pytest.fixture
def realm(signing) -> Realm:
    return Realm([public_jwk(signing, KID)], identity_token(signing))


@pytest.fixture
def party(realm) -> RelyingParty:
    return RelyingParty(
        issuer=ISSUER,
        client_id=CLIENT,
        client_secret="a-secret",
        redirect_uri=REDIRECT,
        keys=SigningKeys(ISSUER, realm.client()),
        http=realm.client(),
    )


class TestSendingSomebodyToSignIn:
    async def test_it_goes_to_the_authorization_endpoint(self, party):
        where = await party.authorization_url(Handshake.begin())

        assert where.startswith(
            "https://keycloak.streamstech.com/realms/streamstech/protocol/openid-connect/auth?"
        )

    async def test_it_asks_for_a_code_on_behalf_of_this_client(self, party):
        query = httpx.QueryParams(
            (await party.authorization_url(Handshake.begin())).split("?", 1)[1]
        )

        assert query["response_type"] == "code"
        assert query["client_id"] == CLIENT
        assert query["redirect_uri"] == REDIRECT

    async def test_it_carries_a_pkce_challenge(self, party):
        handshake = Handshake.begin()

        query = httpx.QueryParams((await party.authorization_url(handshake)).split("?", 1)[1])

        assert query["code_challenge_method"] == "S256"
        assert query["code_challenge"] == handshake.challenge()

    def test_the_challenge_is_the_digest_of_the_verifier_and_not_the_verifier(self):
        handshake = Handshake.begin()
        expected = (
            base64.urlsafe_b64encode(hashlib.sha256(handshake.verifier.encode()).digest())
            .decode()
            .rstrip("=")
        )

        assert handshake.challenge() == expected
        assert handshake.challenge() != handshake.verifier

    def test_two_handshakes_share_nothing(self):
        first, second = Handshake.begin(), Handshake.begin()

        assert first.state != second.state
        assert first.verifier != second.verifier


class TestComingBack:
    async def test_a_good_callback_says_who_it_is(self, party):
        handshake = Handshake.begin()

        signed_in = await party.finish("the-code", handshake, state=handshake.state)

        assert signed_in.subject == SUBJECT
        assert signed_in.name == "Alice Example"
        assert signed_in.email == "alice@streamstech.com"

    async def test_the_person_matches_what_the_token_check_computes(self, party):
        handshake = Handshake.begin()

        signed_in = await party.finish("the-code", handshake, state=handshake.state)

        assert signed_in.person == person_id(ISSUER, SUBJECT)

    async def test_the_verifier_is_sent_and_the_challenge_is_not(self, party, realm):
        handshake = Handshake.begin()

        await party.finish("the-code", handshake, state=handshake.state)

        sent = realm.exchanges[-1]
        assert sent["code_verifier"] == handshake.verifier
        assert sent["grant_type"] == "authorization_code"
        assert sent["redirect_uri"] == REDIRECT

    async def test_a_mismatched_state_is_refused_before_anything_is_exchanged(self, party, realm):
        """Without this, a login somebody else started can be completed in this browser."""
        handshake = Handshake.begin()

        with pytest.raises(LoginFailed, match="did not start here"):
            await party.finish("the-code", handshake, state="not-the-state-we-issued")

        assert realm.exchanges == []

    async def test_an_absent_state_is_refused(self, party):
        handshake = Handshake.begin()

        with pytest.raises(LoginFailed):
            await party.finish("the-code", handshake, state="")


class TestIdentitiesThatMustNotBeBelieved:
    async def test_one_minted_for_a_different_client(self, party, realm, signing):
        realm.identity = identity_token(signing, aud="some-other-client")
        handshake = Handshake.begin()

        with pytest.raises(LoginFailed, match="could not be verified"):
            await party.finish("the-code", handshake, state=handshake.state)

    async def test_one_from_a_different_issuer(self, party, realm, signing):
        realm.identity = identity_token(signing, iss="https://elsewhere.example/realms/x")
        handshake = Handshake.begin()

        with pytest.raises(LoginFailed):
            await party.finish("the-code", handshake, state=handshake.state)

    async def test_an_expired_one(self, party, realm, signing):
        realm.identity = identity_token(signing, exp=datetime.now(UTC) - timedelta(minutes=1))
        handshake = Handshake.begin()

        with pytest.raises(LoginFailed):
            await party.finish("the-code", handshake, state=handshake.state)

    async def test_one_signed_by_a_key_the_realm_does_not_publish(self, party, realm):
        realm.identity = identity_token(new_key())
        handshake = Handshake.begin()

        with pytest.raises(Exception) as caught:
            await party.finish("the-code", handshake, state=handshake.state)

        assert "publish" in str(caught.value) or "verified" in str(caught.value)

    async def test_an_unsigned_one(self, party, realm):
        import json

        def b64(payload: dict) -> str:
            return base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")

        realm.identity = f"{b64({'alg': 'none', 'kid': KID})}.{b64({'sub': SUBJECT})}."
        handshake = Handshake.begin()

        with pytest.raises(LoginFailed, match="algorithm"):
            await party.finish("the-code", handshake, state=handshake.state)

    async def test_no_identity_at_all(self, party, realm):
        realm.identity = ""
        handshake = Handshake.begin()

        with pytest.raises(LoginFailed, match="no identity"):
            await party.finish("the-code", handshake, state=handshake.state)


class TestWhenItGoesWrong:
    async def test_a_refused_exchange_does_not_repeat_what_the_realm_said(self, party, realm):
        """The body can carry the authorization code, and this message reaches a page."""
        realm.refuses = True
        handshake = Handshake.begin()

        with pytest.raises(LoginFailed) as caught:
            await party.finish("the-code", handshake, state=handshake.state)

        assert "leaked-if-shown" not in str(caught.value)
        assert "invalid_grant" not in str(caught.value)

    async def test_an_unreachable_realm_is_not_a_login_failure(self, party, realm):
        realm.unreachable = True
        handshake = Handshake.begin()

        with pytest.raises(IssuerUnreachable):
            await party.finish("the-code", handshake, state=handshake.state)

    def test_a_logged_in_person_does_not_print_their_email(self):
        from bitbucket_pr_review_mcp.oidc import LoggedIn

        signed_in = LoggedIn(
            subject=SUBJECT, issuer=ISSUER, name="Alice", email="alice@streamstech.com"
        )

        assert "alice@streamstech.com" not in repr(signed_in)

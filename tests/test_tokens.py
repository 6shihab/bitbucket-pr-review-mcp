"""Verifying an access token, with real keys and real signatures.

The seam is the network and nothing else, as everywhere in this suite: the JWTs here are
genuinely signed with a genuinely generated RSA key, and only the authorization server's
HTTP endpoints are mocked. A test that signed nothing would prove nothing — the two
checks worth having are exactly the two a fake would skip.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from datetime import UTC, datetime, timedelta

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from bitbucket_pr_review_mcp.discovery import REVIEW_SCOPE, ProtectedResource
from bitbucket_pr_review_mcp.tokens import (
    InsufficientScope,
    IssuerUnreachable,
    SigningKeys,
    TokenRejected,
    TokenVerifier,
)

PUBLIC = "https://review.streamstech.com/mcp"
ISSUER = "https://keycloak.streamstech.com/realms/streamstech"
JWKS_URI = "https://keycloak.streamstech.com/realms/streamstech/protocol/openid-connect/certs"
SUBJECT = "f:9c1e:alice"

KID = "the-current-key"
ROTATED = "the-next-key"


@pytest.fixture(scope="module")
def signing():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture(scope="module")
def rotated():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def public_jwk(private, kid: str) -> dict:
    entry = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(private.public_key()))
    return {**entry, "kid": kid, "alg": "RS256", "use": "sig"}


def token_for(private, kid: str = KID, **overrides) -> str:
    claims = {
        "iss": ISSUER,
        "sub": SUBJECT,
        "aud": PUBLIC,
        "exp": datetime.now(UTC) + timedelta(minutes=5),
        "iat": datetime.now(UTC),
        "scope": f"openid {REVIEW_SCOPE}",
    }
    claims.update(overrides)
    return jwt.encode(claims, private, algorithm="RS256", headers={"kid": kid})


class Keycloak:
    """The authorization server's two public endpoints, and a count of the asking."""

    def __init__(self, keys: list[dict]) -> None:
        self.keys = keys
        self.metadata_hits = 0
        self.jwks_hits = 0
        self.openid_configuration_missing = False
        self.declared_issuer = ISSUER

    def handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/.well-known/openid-configuration"):
            if self.openid_configuration_missing:
                return httpx.Response(404)
            self.metadata_hits += 1
            return httpx.Response(200, json={"issuer": self.declared_issuer, "jwks_uri": JWKS_URI})
        if path.startswith("/.well-known/oauth-authorization-server"):
            self.metadata_hits += 1
            return httpx.Response(200, json={"issuer": self.declared_issuer, "jwks_uri": JWKS_URI})
        if request.url == JWKS_URI:
            self.jwks_hits += 1
            return httpx.Response(200, json={"keys": self.keys})
        return httpx.Response(404)

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(self.handle))


@pytest.fixture
def keycloak(signing) -> Keycloak:
    return Keycloak([public_jwk(signing, KID)])


@pytest.fixture
def resource() -> ProtectedResource:
    return ProtectedResource.of(PUBLIC, ISSUER)


@pytest.fixture
def verifier(keycloak, resource) -> TokenVerifier:
    return TokenVerifier(resource=resource, keys=SigningKeys(ISSUER, keycloak.client()))


class TestAGoodToken:
    async def test_it_says_who_is_calling(self, verifier, signing):
        caller = await verifier.verify(f"Bearer {token_for(signing)}")

        assert caller.subject == SUBJECT
        assert caller.issuer == ISSUER

    async def test_it_carries_the_scopes_that_were_granted(self, verifier, signing):
        caller = await verifier.verify(f"Bearer {token_for(signing)}")

        assert REVIEW_SCOPE in caller.scopes

    async def test_the_person_is_stable_across_calls(self, verifier, signing):
        first = await verifier.verify(f"Bearer {token_for(signing)}")
        second = await verifier.verify(f"Bearer {token_for(signing)}")

        assert first.person == second.person

    async def test_the_same_subject_from_another_issuer_is_another_person(self, verifier, signing):
        """Two authorization servers can both call somebody 'alice'. They are not the
        same person, and their credentials must not land in the same vault row."""
        caller = await verifier.verify(f"Bearer {token_for(signing)}")
        elsewhere = type(caller)(
            subject=caller.subject,
            issuer="https://someone-elses-keycloak.example/realms/x",
            scopes=caller.scopes,
            expires_at=caller.expires_at,
        )

        assert caller.person != elsewhere.person

    async def test_the_keys_are_fetched_once_and_reused(self, verifier, signing, keycloak):
        await verifier.verify(f"Bearer {token_for(signing)}")
        await verifier.verify(f"Bearer {token_for(signing)}")

        assert keycloak.jwks_hits == 1

    async def test_the_subject_is_not_the_vault_key(self, verifier, signing):
        """The vault stores this column in clear. A digest keeps a stolen file from
        also being a directory of user ids."""
        caller = await verifier.verify(f"Bearer {token_for(signing)}")

        assert SUBJECT not in caller.person
        assert len(caller.person) == 64


class TestTokensThatMustNotGetIn:
    async def test_nothing_presented(self, verifier):
        for nothing in [None, "", "   "]:
            with pytest.raises(TokenRejected):
                await verifier.verify(nothing)

    async def test_the_wrong_scheme(self, verifier, signing):
        with pytest.raises(TokenRejected):
            await verifier.verify(f"Basic {token_for(signing)}")

    async def test_a_bearer_with_nothing_after_it(self, verifier):
        with pytest.raises(TokenRejected):
            await verifier.verify("Bearer ")

    async def test_something_that_is_not_a_token_at_all(self, verifier):
        with pytest.raises(TokenRejected):
            await verifier.verify("Bearer not.a.jwt")

    async def test_an_expired_one(self, verifier, signing):
        stale = token_for(signing, exp=datetime.now(UTC) - timedelta(seconds=1))

        with pytest.raises(TokenRejected, match="expired"):
            await verifier.verify(f"Bearer {stale}")

    async def test_one_minted_for_somewhere_else(self, verifier, signing):
        """The check RFC 8707 exists for. Without it, a token another service issued for
        itself is accepted here and its bearer gets somebody's Bitbucket credential."""
        elsewhere = token_for(signing, aud="https://some-other-service.example")

        with pytest.raises(TokenRejected, match="audience"):
            await verifier.verify(f"Bearer {elsewhere}")

    async def test_one_from_another_issuer(self, verifier, signing):
        foreign = token_for(signing, iss="https://someone-elses-keycloak.example/realms/x")

        with pytest.raises(TokenRejected):
            await verifier.verify(f"Bearer {foreign}")

    async def test_one_with_no_subject(self, verifier, signing):
        with pytest.raises(TokenRejected):
            await verifier.verify(f"Bearer {token_for(signing, sub=None)}")

    async def test_one_signed_by_a_key_the_issuer_does_not_publish(self, verifier, rotated):
        with pytest.raises(TokenRejected):
            await verifier.verify(f"Bearer {token_for(rotated)}")

    async def test_one_that_grants_too_little(self, verifier, signing):
        thin = token_for(signing, scope="openid profile")

        with pytest.raises(InsufficientScope, match=REVIEW_SCOPE):
            await verifier.verify(f"Bearer {thin}")


class TestTheAlgorithmAllowlist:
    """Both classic forgeries. Neither reaches a key lookup."""

    async def test_alg_none_is_refused(self, verifier):
        header = _b64({"alg": "none", "kid": KID})
        claims = _b64({"iss": ISSUER, "sub": SUBJECT, "aud": PUBLIC, "exp": 4102444800})
        forged = f"{header}.{claims}."

        with pytest.raises(TokenRejected, match="algorithm"):
            await verifier.verify(f"Bearer {forged}")

    async def test_an_hmac_signed_with_the_public_key_is_refused(self, verifier, signing):
        """The issuer's verification key is public. If HS256 were accepted, anyone who
        can read it could mint tokens with it."""
        public = signing.public_key().public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        # Crafted by hand: PyJWT refuses to *sign* with a PEM as an HMAC secret, and an
        # attacker has no such scruples. This is the token they would actually send.
        signing_input = (
            _b64({"alg": "HS256", "kid": KID})
            + "."
            + _b64({"iss": ISSUER, "sub": SUBJECT, "aud": PUBLIC, "exp": 4102444800})
        )
        signature = hmac.new(public, signing_input.encode(), hashlib.sha256).digest()
        forged = signing_input + "." + base64.urlsafe_b64encode(signature).decode().rstrip("=")

        with pytest.raises(TokenRejected, match="algorithm"):
            await verifier.verify(f"Bearer {forged}")

    async def test_the_refusal_does_not_say_which_attack_was_tried(self, verifier):
        header = _b64({"alg": "none", "kid": KID})
        forged = f"{header}.{_b64({'sub': SUBJECT})}."

        with pytest.raises(TokenRejected) as caught:
            await verifier.verify(f"Bearer {forged}")

        assert "none" not in str(caught.value)


class TestKeyRotation:
    async def test_an_unknown_key_id_causes_one_refetch(self, keycloak, resource, signing, rotated):
        clock = _Clock()
        keys = SigningKeys(ISSUER, keycloak.client(), now=clock)
        verifier = TokenVerifier(resource=resource, keys=keys)
        await verifier.verify(f"Bearer {token_for(signing)}")

        keycloak.keys = [public_jwk(rotated, ROTATED)]
        clock.advance(120)
        caller = await verifier.verify(f"Bearer {token_for(rotated, kid=ROTATED)}")

        assert caller.subject == SUBJECT
        assert keycloak.jwks_hits == 2

    async def test_rubbish_key_ids_cannot_be_used_to_hammer_the_issuer(
        self, keycloak, resource, signing
    ):
        clock = _Clock()
        keys = SigningKeys(ISSUER, keycloak.client(), now=clock)
        verifier = TokenVerifier(resource=resource, keys=keys)
        await verifier.verify(f"Bearer {token_for(signing)}")

        for _ in range(20):
            with pytest.raises(TokenRejected):
                await verifier.verify(f"Bearer {token_for(signing, kid='invented')}")

        assert keycloak.jwks_hits == 1


class TestFindingTheIssuersKeys:
    async def test_openid_discovery_is_tried_first(self, verifier, signing, keycloak):
        await verifier.verify(f"Bearer {token_for(signing)}")

        assert keycloak.metadata_hits == 1

    async def test_it_falls_back_to_rfc_8414(self, keycloak, resource, signing):
        """Which inserts the well-known segment *before* the issuer's path — the
        opposite of what most people expect, and a quiet failure against realms."""
        keycloak.openid_configuration_missing = True
        verifier = TokenVerifier(resource=resource, keys=SigningKeys(ISSUER, keycloak.client()))

        caller = await verifier.verify(f"Bearer {token_for(signing)}")

        assert caller.subject == SUBJECT

    async def test_metadata_claiming_a_different_issuer_is_refused(
        self, keycloak, resource, signing
    ):
        """RFC 8414's mix-up defence: if the document does not name the issuer we asked
        about, something is answering for somebody else."""
        keycloak.declared_issuer = "https://not-the-one-we-configured.example"
        verifier = TokenVerifier(resource=resource, keys=SigningKeys(ISSUER, keycloak.client()))

        with pytest.raises(IssuerUnreachable, match="match"):
            await verifier.verify(f"Bearer {token_for(signing)}")

    async def test_an_unreachable_issuer_is_not_the_callers_fault(self, resource, signing):
        def refuse(request: httpx.Request) -> httpx.Response:
            return httpx.Response(503)

        keys = SigningKeys(ISSUER, httpx.AsyncClient(transport=httpx.MockTransport(refuse)))
        verifier = TokenVerifier(resource=resource, keys=keys)

        with pytest.raises(IssuerUnreachable):
            await verifier.verify(f"Bearer {token_for(signing)}")


class TestNothingItSaysCarriesTheToken:
    async def test_not_in_any_refusal(self, verifier, signing):
        attempts = [
            None,
            "Bearer not.a.jwt",
            f"Bearer {token_for(signing, exp=datetime.now(UTC) - timedelta(days=1))}",
            f"Bearer {token_for(signing, aud='https://elsewhere.example')}",
            f"Bearer {token_for(signing, scope='openid')}",
        ]

        for presented in attempts:
            with pytest.raises(TokenRejected) as caught:
                await verifier.verify(presented)

            if presented:
                assert presented.removeprefix("Bearer ") not in str(caught.value)

    async def test_a_caller_does_not_print_its_scopes_or_expiry_secrets(self, verifier, signing):
        caller = await verifier.verify(f"Bearer {token_for(signing)}")

        assert token_for(signing) not in repr(caller)
        assert caller.subject in repr(caller)


class _Clock:
    def __init__(self) -> None:
        self.at = 1000.0

    def __call__(self) -> float:
        return self.at

    def advance(self, seconds: float) -> None:
        self.at += seconds


def _b64(payload: dict) -> str:
    raw = json.dumps(payload).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")

"""A real authorization server, minus the network.

The keys are genuinely generated and the tokens genuinely signed — only Keycloak's two
HTTP endpoints are mocked. Shared by the token tests and the transport tests so that
"a valid token" means the same thing in both.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import httpx
import jwt
from cryptography.hazmat.primitives.asymmetric import rsa

from bitbucket_pr_review_mcp.discovery import REVIEW_SCOPE

ISSUER = "https://keycloak.streamstech.com/realms/streamstech"
JWKS_URI = "https://keycloak.streamstech.com/realms/streamstech/protocol/openid-connect/certs"
AUTHORIZE = "https://keycloak.streamstech.com/realms/streamstech/protocol/openid-connect/auth"
TOKEN_ENDPOINT = (
    "https://keycloak.streamstech.com/realms/streamstech/protocol/openid-connect/token"
)
RESOURCE = "https://review.streamstech.com/mcp"
SUBJECT = "f:9c1e:alice"

KID = "the-current-key"
ROTATED = "the-next-key"


def new_key() -> rsa.RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def public_jwk(private, kid: str) -> dict:
    entry = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(private.public_key()))
    return {**entry, "kid": kid, "alg": "RS256", "use": "sig"}


def token_for(private, kid: str = KID, *, audience: str = RESOURCE, **overrides) -> str:
    claims = {
        "iss": ISSUER,
        "sub": SUBJECT,
        "aud": audience,
        "exp": datetime.now(UTC) + timedelta(minutes=5),
        "iat": datetime.now(UTC),
        "scope": f"openid {REVIEW_SCOPE}",
    }
    claims.update(overrides)
    # A claim set to None is *dropped*, not sent as null: the interesting token is the
    # one a realm without an audience mapper actually mints, which has no `aud` key.
    claims = {name: value for name, value in claims.items() if value is not None}
    return jwt.encode(claims, private, algorithm="RS256", headers={"kid": kid})


class Keycloak:
    """The authorization server's two public endpoints, and a count of the asking."""

    def __init__(self, keys: list[dict]) -> None:
        self.keys = keys
        self.metadata_hits = 0
        self.jwks_hits = 0
        self.openid_configuration_missing = False
        self.unreachable = False
        self.declared_issuer = ISSUER

    def document(self) -> dict:
        return {
            "issuer": self.declared_issuer,
            "jwks_uri": JWKS_URI,
            "authorization_endpoint": AUTHORIZE,
            "token_endpoint": TOKEN_ENDPOINT,
            "code_challenge_methods_supported": ["S256"],
        }

    def handle(self, request: httpx.Request) -> httpx.Response:
        if self.unreachable:
            return httpx.Response(503)

        path = request.url.path
        if path.endswith("/.well-known/openid-configuration"):
            if self.openid_configuration_missing:
                return httpx.Response(404)
            self.metadata_hits += 1
            return httpx.Response(200, json=self.document())
        if path.startswith("/.well-known/oauth-authorization-server"):
            self.metadata_hits += 1
            return httpx.Response(200, json=self.document())
        if request.url == JWKS_URI:
            self.jwks_hits += 1
            return httpx.Response(200, json={"keys": self.keys})
        return httpx.Response(404)

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(self.handle))

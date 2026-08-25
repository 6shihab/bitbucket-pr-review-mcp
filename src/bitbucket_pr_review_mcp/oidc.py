"""Logging somebody in with Keycloak, so that the credential page is not a capability.

This is the other side of `tokens.py`. There, an access token arrives and this server
checks it. Here, a *browser* arrives and this server sends it to Keycloak to find out who
is driving it.

That distinction is the whole of ticket 13. The page that collects an Atlassian API token
cannot be reachable by whoever holds its URL: a link in a tool's answer travels through
the model's transcript, and anybody who reads it there could otherwise open it and
**supply** a credential — their token, stored as somebody else's, so that person's review
posts under the attacker's name. Behind a login the link stops being a capability.
Whoever opens it authenticates as themselves and connects their own account.

What is implemented is the small half of OAuth: authorization code with PKCE, one token
exchange, one ID token verified. Keycloak does the difficult half, which is the reason
ADR-0008's sibling decision was to let it.
"""

from __future__ import annotations

import base64
import hashlib
import secrets
from dataclasses import dataclass

import httpx
import jwt
from jwt import InvalidTokenError
from loguru import logger

from .tokens import ALLOWED_ALGORITHMS, IssuerUnreachable, SigningKeys, person_id

# Enough entropy that guessing one is not a strategy. `state` is the CSRF defence on the
# callback and the verifier is what makes an intercepted code useless.
ENTROPY_BYTES = 32

SCOPES = "openid profile email"


class LoginFailed(RuntimeError):
    """The browser does not get a session. The message is safe to show a person."""


@dataclass(frozen=True, slots=True)
class Handshake:
    """The two secrets a login carries, held by the server between the two requests."""

    state: str
    verifier: str

    @classmethod
    def begin(cls) -> Handshake:
        return cls(state=_random(), verifier=_random())

    def challenge(self) -> str:
        digest = hashlib.sha256(self.verifier.encode("ascii")).digest()
        return _b64(digest)


@dataclass(frozen=True, slots=True)
class LoggedIn:
    """Who the browser belongs to."""

    subject: str
    issuer: str
    name: str
    email: str | None = None

    @property
    def person(self) -> str:
        """The same vault key the access token check computes. It has to be: a credential
        stored under a different one is a credential nothing ever finds."""
        return person_id(self.issuer, self.subject)

    def __repr__(self) -> str:
        return f"LoggedIn(subject={self.subject!r}, name={self.name!r})"


@dataclass(frozen=True, slots=True)
class RelyingParty:
    """This server, as an ordinary OAuth client of Keycloak."""

    issuer: str
    client_id: str
    client_secret: str
    redirect_uri: str
    keys: SigningKeys
    http: httpx.AsyncClient

    async def authorization_url(self, handshake: Handshake) -> str:
        endpoint = await self.keys.endpoint("authorization_endpoint")
        query = httpx.QueryParams(
            {
                "response_type": "code",
                "client_id": self.client_id,
                "redirect_uri": self.redirect_uri,
                "scope": SCOPES,
                "state": handshake.state,
                "code_challenge": handshake.challenge(),
                "code_challenge_method": "S256",
            }
        )
        return f"{endpoint}?{query}"

    async def finish(self, code: str, handshake: Handshake, *, state: str) -> LoggedIn:
        """Turn a callback into a person, refusing everything that is not exactly right."""
        if not secrets.compare_digest(state, handshake.state):
            # The one check that stops somebody else's login landing in this browser.
            raise LoginFailed("This sign-in did not start here. Open the page again.")

        endpoint = await self.keys.endpoint("token_endpoint")
        try:
            response = await self.http.post(
                endpoint,
                data={
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": self.redirect_uri,
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                    "code_verifier": handshake.verifier,
                },
                headers={"content-type": "application/x-www-form-urlencoded"},
            )
        except httpx.HTTPError as exc:
            raise IssuerUnreachable(f"Could not reach {endpoint}: {exc}") from exc

        if response.status_code != 200:
            # Never the body: it can carry the code, and this ends up in a page.
            logger.warning("Token exchange refused with {}.", response.status_code)
            raise LoginFailed("Signing in did not complete. Try again.")

        identity = response.json().get("id_token")
        if not identity:
            raise LoginFailed("The authorization server returned no identity to check.")
        return await self._read(identity)

    async def _read(self, identity: str) -> LoggedIn:
        try:
            header = jwt.get_unverified_header(identity)
        except InvalidTokenError as exc:
            raise LoginFailed("The identity returned is unreadable.") from exc

        if header.get("alg") not in ALLOWED_ALGORITHMS:
            raise LoginFailed("The identity is signed with an algorithm this server refuses.")

        key = await self.keys.for_key_id(header.get("kid"))
        try:
            claims = jwt.decode(
                identity,
                key=key.key,
                algorithms=list(ALLOWED_ALGORITHMS),
                issuer=self.issuer,
                audience=self.client_id,
                options={"require": ["exp", "iss", "sub", "aud"]},
            )
        except InvalidTokenError as exc:
            raise LoginFailed("The identity returned could not be verified.") from exc

        subject = str(claims["sub"])
        return LoggedIn(
            subject=subject,
            issuer=str(claims["iss"]),
            name=str(claims.get("name") or claims.get("preferred_username") or subject),
            email=claims.get("email"),
        )


def _random() -> str:
    return _b64(secrets.token_bytes(ENTROPY_BYTES))


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


__all__ = ["SCOPES", "Handshake", "LoggedIn", "LoginFailed", "RelyingParty"]

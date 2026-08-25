"""Who is calling: an access token, verified, turned into a person.

This is the half of ticket 10 that does not depend on where the tokens come from. Whether
Keycloak issues them or something else does, the work is the same — check the signature
against keys the issuer publishes, check the token was minted *for us*, and turn the
subject into the vault key that finds this person's Bitbucket credential.

Two checks here are load-bearing and both are easy to leave out:

**The algorithm allowlist.** A verifier that trusts the token's own `alg` header can be
handed `none`, or handed `HS256` and made to verify a symmetric signature using the
issuer's *public* key — which anybody can read. Only asymmetric algorithms are accepted,
named here rather than taken from the token.

**The audience.** A token is a bearer credential for one resource. Without an audience
check, a token some other service issued for itself — or one this issuer minted for a
different client — is accepted here, and the caller gets somebody's Bitbucket credential
on the strength of a token that was never meant for this server. RFC 8707 exists for this,
and the MCP specification makes it mandatory.

Nothing in this module logs a token, and nothing puts one in an exception message: a
rejection travels back to a client and, on a bad day, into a transcript.
"""

from __future__ import annotations

import hashlib
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from urllib.parse import urlsplit

import httpx
import jwt
from jwt import InvalidTokenError, PyJWKSet
from loguru import logger

from .discovery import ProtectedResource

# Asymmetric only. Never `none`, and never an HMAC family: the issuer's verification key
# is public, so a symmetric algorithm turns "anyone can read the key" into "anyone can
# mint a token".
ALLOWED_ALGORITHMS = (
    "RS256",
    "RS384",
    "RS512",
    "PS256",
    "PS384",
    "PS512",
    "ES256",
    "ES384",
    "ES512",
)

REQUIRED_CLAIMS = ["exp", "iss", "sub", "aud"]

OPENID_CONFIGURATION = "/.well-known/openid-configuration"
OAUTH_METADATA = "/.well-known/oauth-authorization-server"

# An unknown key id means the issuer has rotated. Refetching is right; refetching on
# every unknown id is a denial of service somebody else can trigger by sending rubbish.
MIN_SECONDS_BETWEEN_REFETCHES = 60.0


class TokenRejected(RuntimeError):
    """The caller does not get in. Answer 401, with a challenge saying where to go."""


class InsufficientScope(TokenRejected):
    """A valid token that is not permitted to do this. Answer 403."""


class IssuerUnreachable(RuntimeError):
    """The authorization server could not be consulted. Not the caller's fault."""


def person_id(issuer: str, subject: str) -> str:
    """The vault key for somebody, from the only two things that identify them.

    Both doors compute this: the token check on every MCP request, and the browser login
    that stores the credential. They must agree exactly — if they ever drift, credentials
    are written to a row nothing reads, and the symptom is "setup keeps asking me".

    Hashed rather than stored as `issuer#subject` because the vault keeps this column in
    clear; it is what the threat model admits a stolen database file reveals. A digest
    keeps that admission to "how many people are enrolled" instead of also handing over
    an issuer and a directory of user ids.
    """
    return hashlib.sha256(f"{issuer}\x00{subject}".encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class Caller:
    """One authenticated person, for the life of one request."""

    subject: str
    issuer: str
    scopes: frozenset[str]
    expires_at: datetime

    @property
    def person(self) -> str:
        """The vault key for this person.

        Hashed rather than stored as `issuer#subject` because the vault keeps this
        column in clear — it is what the threat model admits a stolen database file
        reveals. A digest keeps that admission to "how many people are enrolled" instead
        of also handing over an issuer and a directory of user ids.
        """
        return hashlib.sha256(f"{self.issuer}\x00{self.subject}".encode()).hexdigest()

    def __repr__(self) -> str:
        return f"Caller(subject={self.subject!r}, issuer={self.issuer!r})"


@dataclass
class SigningKeys:
    """The authorization server's public keys, fetched once and refreshed on rotation."""

    issuer: str
    http: httpx.AsyncClient
    now: Callable[[], float] = time.monotonic
    _keys: PyJWKSet | None = field(default=None, repr=False)
    _metadata: dict | None = field(default=None, repr=False)
    _fetched_at: float = field(default=0.0, repr=False)

    async def for_key_id(self, key_id: str | None):
        keys = self._keys or await self._fetch()
        found = _find(keys, key_id)
        if found is not None:
            return found

        if self.now() - self._fetched_at < MIN_SECONDS_BETWEEN_REFETCHES:
            raise TokenRejected(
                "This token is signed with a key the authorization server does not "
                "publish. If its keys were just rotated, this resolves itself shortly."
            )

        found = _find(await self._fetch(), key_id)
        if found is None:
            raise TokenRejected(
                "This token is signed with a key the authorization server does not "
                "publish, so it cannot be verified."
            )
        return found

    async def metadata(self) -> dict:
        """The authorization server's discovery document, fetched once."""
        if self._metadata is None:
            await self._discover()
        assert self._metadata is not None
        return self._metadata

    async def endpoint(self, named: str) -> str:
        document = await self.metadata()
        found = document.get(named)
        if not isinstance(found, str) or not found:
            raise IssuerUnreachable(
                f"{self.issuer} publishes no {named}, so this server cannot use it."
            )
        return found

    async def _fetch(self) -> PyJWKSet:
        uri = (self._metadata or {}).get("jwks_uri") or await self._discover()
        payload = await self._get(uri, "the authorization server's keys")
        try:
            self._keys = PyJWKSet.from_dict(payload)
        except (InvalidTokenError, KeyError, TypeError, AttributeError) as exc:
            raise IssuerUnreachable(f"{uri} did not answer with a usable JWKS: {exc}") from exc
        self._fetched_at = self.now()
        logger.debug("Fetched {} signing key(s) from {}.", len(self._keys.keys), uri)
        return self._keys

    async def _discover(self) -> str:
        """Find `jwks_uri`, trying OpenID Discovery before RFC 8414.

        Keycloak serves both. The RFC 8414 form inserts the well-known segment *before*
        the issuer's path, which is the opposite of what most people expect and the
        reason discovery quietly fails against realm-scoped issuers.
        """
        split = urlsplit(self.issuer)
        candidates = (
            f"{self.issuer.rstrip('/')}{OPENID_CONFIGURATION}",
            f"{split.scheme}://{split.netloc}{OAUTH_METADATA}{split.path.rstrip('/')}",
        )

        last: Exception | None = None
        for candidate in candidates:
            try:
                document = await self._get(candidate, "the authorization server's metadata")
            except IssuerUnreachable as exc:
                last = exc
                continue

            declared = str(document.get("issuer", ""))
            if declared != self.issuer:
                raise IssuerUnreachable(
                    f"{candidate} says its issuer is {declared!r}, but this server was "
                    f"configured for {self.issuer!r}. Those must match exactly, or a "
                    "token from somewhere else could be accepted here."
                )

            uri = document.get("jwks_uri")
            if not isinstance(uri, str) or not uri:
                raise IssuerUnreachable(f"{candidate} names no jwks_uri.")
            self._metadata = document
            return uri

        raise IssuerUnreachable(
            f"Could not read authorization server metadata for {self.issuer}. Tried "
            f"{' and '.join(candidates)}. ({last})"
        )

    async def _get(self, url: str, what: str) -> dict:
        try:
            response = await self.http.get(url, headers={"accept": "application/json"})
            response.raise_for_status()
            return response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise IssuerUnreachable(f"Could not read {what} from {url}: {exc}") from exc


@dataclass(frozen=True, slots=True)
class TokenVerifier:
    """Turns an `Authorization` header into a `Caller`, or into a refusal."""

    resource: ProtectedResource
    keys: SigningKeys

    async def verify(self, presented: str | None) -> Caller:
        token = _bearer(presented)

        try:
            header = jwt.get_unverified_header(token)
        except InvalidTokenError as exc:
            raise TokenRejected("This is not a usable access token.") from exc

        algorithm = header.get("alg")
        if algorithm not in ALLOWED_ALGORITHMS:
            # Said without naming what was asked for: the answer goes back to whoever
            # sent it, and there is no reason to confirm which attack was tried.
            raise TokenRejected("This token is signed with an algorithm this server refuses.")

        key = await self.keys.for_key_id(header.get("kid"))

        try:
            claims = jwt.decode(
                token,
                key=key.key,
                algorithms=list(ALLOWED_ALGORITHMS),
                issuer=self.keys.issuer,
                audience=self.resource.resource,
                options={"require": REQUIRED_CLAIMS, "verify_aud": True},
            )
        except jwt.ExpiredSignatureError as exc:
            raise TokenRejected("This access token has expired.") from exc
        except jwt.MissingRequiredClaimError as exc:
            # Worth naming. A token with no audience at all is almost always a realm that
            # is not adding one, and "could not be verified" sends whoever is debugging
            # it to look at signatures instead.
            raise TokenRejected(
                f"This access token carries no {exc.claim!r} claim, so it cannot be "
                "accepted. If tokens are missing an audience, the authorization server "
                "is not adding one — Keycloak needs an audience mapper on the scope this "
                "server requires, because it does not implement RFC 8707."
            ) from exc
        except jwt.InvalidAudienceError as exc:
            raise TokenRejected(
                "This access token was not issued for this server. It names a different "
                "audience, and a token minted for somewhere else is not accepted here."
            ) from exc
        except InvalidTokenError as exc:
            raise TokenRejected("This access token could not be verified.") from exc

        caller = Caller(
            subject=str(claims["sub"]),
            issuer=str(claims["iss"]),
            scopes=_scopes(claims),
            expires_at=datetime.fromtimestamp(int(claims["exp"]), tz=UTC),
        )

        missing = set(self.resource.scopes) - caller.scopes
        if missing:
            raise InsufficientScope(
                f"This token does not carry {' '.join(sorted(missing))}, which this "
                "server requires. Reconnect the connector to grant it."
            )
        return caller


def _bearer(presented: str | None) -> str:
    value = (presented or "").strip()
    if not value:
        raise TokenRejected("This server needs an access token, and none was presented.")

    scheme, _, token = value.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise TokenRejected("Access tokens are presented as 'Authorization: Bearer <token>'.")
    return token.strip()


def _scopes(claims: dict) -> frozenset[str]:
    """`scope` is the standard, space-delimited. `scp` is a list some issuers send."""
    granted = claims.get("scope")
    if isinstance(granted, str):
        return frozenset(granted.split())

    listed = claims.get("scp")
    if isinstance(listed, list):
        return frozenset(str(entry) for entry in listed)
    return frozenset()


def _find(keys: PyJWKSet, key_id: str | None):
    for key in keys.keys:
        if key_id is None or key.key_id == key_id:
            return key
    return None


__all__ = [
    "ALLOWED_ALGORITHMS",
    "Caller",
    "InsufficientScope",
    "IssuerUnreachable",
    "SigningKeys",
    "TokenRejected",
    "TokenVerifier",
    "person_id",
]

"""Connecting a Bitbucket account to the shared server, behind a Keycloak login.

The per-device server hands out a loopback URL carrying a one-time token, and ADR-0004
accepted that the URL travels through the model's transcript because loopback made
holding it worth nothing. On a server several people share, that acceptance is void — and
the attack is not the obvious one. Whoever reads the link first cannot steal a credential
that is not there yet. They can **supply** one: their token, stored as somebody else's,
so that person's review posts under the attacker's name and every comment lands with the
attacker's account on it.

So the link is not a capability here. It is an ordinary URL, and the page behind it makes
the browser log in. Somebody who reads a link and opens it is asked who they are, and
connects their own account — which is not an attack, it is just them using the server.

Everything that made the loopback page careful is kept: the credential is verified against
Bitbucket before anything is stored, the display name is shown back so a token from the
wrong account is caught by a person rather than by a later 401, and over-broad scopes are
refused at the form.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import html
import json
import secrets
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, timedelta
from urllib.parse import urlsplit

import httpx
from loguru import logger
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import HTMLResponse, RedirectResponse, Response
from starlette.routing import Route

from .client import BitbucketError
from .credentials import CredentialError, StoredCredential
from .oidc import Handshake, LoggedIn, LoginFailed, RelyingParty
from .pages import page
from .scopes import REQUIRED
from .tokens import IssuerUnreachable
from .vault import CredentialVault
from .verify import Identity

TOKEN_PAGE = "https://id.atlassian.com/manage-profile/security/api-tokens"

HANDSHAKE_COOKIE = "bb_login"
SESSION_COOKIE = "bb_who"

HANDSHAKE_SECONDS = 600
SESSION_SECONDS = 3600

# How long a verified-but-unsaved credential waits for somebody to press the button.
PENDING_SECONDS = 300

Verify = Callable[[StoredCredential], "object"]


@dataclass(frozen=True, slots=True)
class ConnectHere:
    """What a tool answers with when the caller has no Bitbucket credential yet.

    Unlike the loopback listener this stands in for, the URL is ordinary and permanent:
    it grants nothing, expires never, and is useless to anybody who is not signed in. So
    the note the Caller is shown says that, rather than repeating the loopback page's
    promise about five minutes and one use.
    """

    url: str

    note = (
        "That page will ask you to sign in first, so a link seen in a transcript is of "
        "no use to anybody but you."
    )

    def start(self, on_saved: Callable | None = None) -> str:
        return self.url

    def stop(self) -> None:
        return None


@dataclass(frozen=True, slots=True)
class Pending:
    """A credential Bitbucket has confirmed, waiting on the person to say "that is me"."""

    at: float
    candidate: StoredCredential
    identity: Identity


def build_connect_app(
    *,
    party: RelyingParty,
    vault: CredentialVault,
    verify: Callable,
    secret: bytes,
    public_url: str,
    on_change: Callable[[str], None] | None = None,
    today: Callable[[], date] = date.today,
    now: Callable[[], float] = time.time,
) -> Starlette:
    """The pages that connect and disconnect one person's Bitbucket account."""
    origin = _origin(public_url)
    secure = origin.startswith("https://")
    pending: dict[str, Pending] = {}

    def changed(person: str) -> None:
        """Say that this person's stored credential is not what it was.

        Without this, a session that has already read one keeps using it: a disconnected
        account goes on posting, and a replaced one posts under the account it replaced.
        """
        if on_change is not None:
            on_change(person)

    def who(request: Request) -> LoggedIn | None:
        payload = _unseal(request.cookies.get(SESSION_COOKIE), secret, SESSION_SECONDS, now)
        if payload is None:
            return None
        return LoggedIn(
            subject=payload["sub"],
            issuer=payload["iss"],
            name=payload["name"],
            email=payload.get("email"),
        )

    async def start(request: Request) -> Response:
        """The link people are given. It grants nothing until somebody signs in."""
        signed_in = who(request)
        if signed_in is not None:
            return HTMLResponse(_form_page(signed_in, vault, today(), _csrf(request, secret)))

        handshake = Handshake.begin()
        try:
            destination = await party.authorization_url(handshake)
        except IssuerUnreachable as exc:
            logger.error("Cannot begin a sign-in: {}", exc)
            return _trouble("The sign-in service is unavailable. Try again shortly.", 503)

        answer = RedirectResponse(destination, status_code=303)
        _set(
            answer,
            HANDSHAKE_COOKIE,
            {"state": handshake.state, "verifier": handshake.verifier},
            secret,
            HANDSHAKE_SECONDS,
            secure,
            now,
        )
        return answer

    async def callback(request: Request) -> Response:
        started = _unseal(request.cookies.get(HANDSHAKE_COOKIE), secret, HANDSHAKE_SECONDS, now)
        if started is None:
            return _trouble("This sign-in took too long. Open the page again.", 400)

        code = request.query_params.get("code")
        if not code:
            # Keycloak says no — a refused consent, or a login that was abandoned.
            return _trouble("Sign-in was not completed.", 400)

        try:
            signed_in = await party.finish(
                code,
                Handshake(state=started["state"], verifier=started["verifier"]),
                state=request.query_params.get("state", ""),
            )
        except LoginFailed as exc:
            return _trouble(str(exc), 400)
        except IssuerUnreachable:
            return _trouble("The sign-in service is unavailable. Try again shortly.", 503)

        answer = RedirectResponse(request.url_for("connect"), status_code=303)
        _set(
            answer,
            SESSION_COOKIE,
            {
                "sub": signed_in.subject,
                "iss": signed_in.issuer,
                "name": signed_in.name,
                "email": signed_in.email,
                "csrf": secrets.token_urlsafe(16),
            },
            secret,
            SESSION_SECONDS,
            secure,
            now,
        )
        answer.delete_cookie(HANDSHAKE_COOKIE, path="/")
        logger.info("{} signed in to connect a Bitbucket account.", signed_in.name)
        return answer

    async def check(request: Request) -> Response:
        """Ask Bitbucket who this token belongs to, and show the answer back. Stores nothing."""
        signed_in, form, refusal = await _posted(request, secret, origin)
        if refusal is not None:
            return refusal
        assert signed_in is not None and form is not None

        try:
            candidate = StoredCredential.of(
                email=str(form.get("email", "")),
                token=str(form.get("api_token", "")),
                expires_on=str(form.get("expires_on", "")),
                today=today(),
            )
        except (CredentialError, ValueError) as exc:
            return _problem(signed_in, vault, today(), _csrf(request, secret), str(exc))

        try:
            identity = await verify(candidate.credential)
        except (BitbucketError, httpx.HTTPError) as exc:
            logger.warning("Verification failed for {}: {}", candidate.email, exc)
            return _problem(signed_in, vault, today(), _csrf(request, secret), str(exc))

        if not identity.scopes.acceptable:
            return _problem(
                signed_in, vault, today(), _csrf(request, secret), identity.scopes.refusal()
            )
        if not identity.scopes.complete:
            return _problem(
                signed_in, vault, today(), _csrf(request, secret), identity.scopes.shortfall()
            )

        pending[signed_in.person] = Pending(now(), candidate, identity)
        return HTMLResponse(_confirm_page(signed_in, candidate, identity, _csrf(request, secret)))

    async def save(request: Request) -> Response:
        signed_in, _, refusal = await _posted(request, secret, origin)
        if refusal is not None:
            return refusal
        assert signed_in is not None

        waiting = pending.pop(signed_in.person, None)
        if waiting is None or now() - waiting.at > PENDING_SECONDS:
            return _problem(
                signed_in,
                vault,
                today(),
                _csrf(request, secret),
                "Nothing has been verified recently. Fill the form in again.",
            )

        vault.save(signed_in.person, waiting.candidate)
        changed(signed_in.person)
        logger.info("{} connected {}.", signed_in.name, waiting.candidate.email)
        return HTMLResponse(_done_page(signed_in, waiting.candidate, waiting.identity))

    async def forget(request: Request) -> Response:
        """Leaving is as easy as joining, and it is a page rather than a tool: a pull
        request description must not be able to talk a Caller into disconnecting anybody."""
        signed_in, _, refusal = await _posted(request, secret, origin)
        if refusal is not None:
            return refusal
        assert signed_in is not None

        pending.pop(signed_in.person, None)
        vault.clear(signed_in.person)
        changed(signed_in.person)
        logger.info("{} disconnected their Bitbucket account.", signed_in.name)
        return HTMLResponse(_forgotten_page(signed_in))

    return Starlette(
        routes=[
            Route("/", start, name="connect"),
            Route("/callback", callback),
            Route("/verify", check, methods=["POST"]),
            Route("/save", save, methods=["POST"]),
            Route("/forget", forget, methods=["POST"]),
        ]
    )


async def _posted(request: Request, secret: bytes, origin: str):
    """Everything a state-changing request has to prove before it is looked at."""
    presented = request.headers.get("origin")
    if presented is not None and presented != origin:
        return None, None, _trouble("That request did not come from this page.", 403)

    payload = _unseal(request.cookies.get(SESSION_COOKIE), secret, SESSION_SECONDS, time.time)
    if payload is None:
        return None, None, _trouble("Your sign-in has expired. Open the page again.", 401)

    form = await request.form()
    if not hmac.compare_digest(str(form.get("csrf", "")), str(payload.get("csrf", ""))):
        return None, None, _trouble("That request did not come from this page.", 403)

    signed_in = LoggedIn(
        subject=payload["sub"],
        issuer=payload["iss"],
        name=payload["name"],
        email=payload.get("email"),
    )
    return signed_in, form, None


def _csrf(request: Request, secret: bytes) -> str:
    payload = _unseal(request.cookies.get(SESSION_COOKIE), secret, SESSION_SECONDS, time.time)
    return str((payload or {}).get("csrf", ""))


def _origin(public_url: str) -> str:
    split = urlsplit(public_url)
    return f"{split.scheme}://{split.netloc}"


def cookie_secret(material: bytes) -> bytes:
    """A cookie-signing key derived from the vault key, so there is one secret to keep.

    Separated by domain so that a signed cookie can never be mistaken for anything else
    the vault key protects, and so that rotating the vault key rotates this too.
    """
    return hashlib.blake2b(material, person=b"bb-mcp/cookie", digest_size=32).digest()


def _set(
    answer: Response,
    name: str,
    payload: dict,
    secret: bytes,
    seconds: int,
    secure: bool,
    now: Callable[[], float],
) -> None:
    answer.set_cookie(
        name,
        _seal(payload, secret, now),
        max_age=seconds,
        httponly=True,
        secure=secure,
        samesite="lax",  # must survive the redirect back from Keycloak
        path="/",
    )


def _seal(payload: dict, secret: bytes, now: Callable[[], float]) -> str:
    body = _b64(json.dumps({**payload, "at": int(now())}, separators=(",", ":")).encode())
    return f"{body}.{_b64(hmac.new(secret, body.encode(), hashlib.sha256).digest())}"


def _unseal(
    cookie: str | None, secret: bytes, seconds: int, now: Callable[[], float]
) -> dict | None:
    if not cookie or "." not in cookie:
        return None

    body, _, signature = cookie.partition(".")
    expected = _b64(hmac.new(secret, body.encode(), hashlib.sha256).digest())
    if not hmac.compare_digest(signature, expected):
        return None

    try:
        payload = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
    except (ValueError, TypeError):
        return None

    if not isinstance(payload, dict) or now() - float(payload.get("at", 0)) > seconds:
        return None
    return payload


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _trouble(message: str, status: int) -> HTMLResponse:
    return HTMLResponse(page("Not connected", f"<p class='bad'>{html.escape(message)}</p>"), status)


def _problem(
    signed_in: LoggedIn, vault: CredentialVault, today: date, csrf: str, message: str
) -> HTMLResponse:
    body = f"<p class='bad'>{html.escape(message)}</p>" + _form(today, csrf)
    return HTMLResponse(page("Connect Bitbucket", _greeting(signed_in) + body), 400)


def _greeting(signed_in: LoggedIn) -> str:
    return (
        f"<p class='note'>Signed in as <strong>{html.escape(signed_in.name)}</strong>. "
        "Whatever you connect here is used only for your own reviews.</p>"
    )


def _form_page(signed_in: LoggedIn, vault: CredentialVault, today: date, csrf: str) -> str:
    already = vault.load(signed_in.person)
    if already is not None:
        return page("Bitbucket connected", _greeting(signed_in) + _connected(already, csrf))
    return page("Connect Bitbucket", _greeting(signed_in) + _intro() + _form(today, csrf))


def _connected(stored: StoredCredential, csrf: str) -> str:
    return f"""
<p>Your Bitbucket account is connected as
<strong>{html.escape(stored.email)}</strong>, until {stored.expires_on.isoformat()}.</p>
<p class="note">Replace it by filling the form in again, or disconnect it below.
Disconnecting removes it from this server and nothing else — the token still exists at
Atlassian until you revoke it there.</p>
<form method="post" action="/connect/forget">
  <input type="hidden" name="csrf" value="{html.escape(csrf)}">
  <button type="submit">Disconnect this account</button>
</form>
<h2>Connect a different account</h2>
"""


def _intro() -> str:
    scopes = "".join(f"<li><code>{html.escape(scope)}</code></li>" for scope in REQUIRED)
    return f"""
<p>This connects your Atlassian account to the review server, so that comments it posts
for you are posted <em>as you</em>. Nobody else on this server can use your token, and it
is encrypted where it is stored.</p>
<h2>1. Create an API token</h2>
<p>Open <a href="{TOKEN_PAGE}" target="_blank" rel="noreferrer">{TOKEN_PAGE}</a>,
create a token, and tick exactly these scopes:</p>
<ul class="scopes">{scopes}</ul>
<p class="note">Nothing wider. A token that can also write to a repository, administer
one, or run pipelines will be refused on the next screen.</p>
<h2>2. Paste it here</h2>
"""


def _form(today: date, csrf: str) -> str:
    default_expiry = (today + timedelta(days=365)).isoformat()
    return f"""
<form method="post" action="/connect/verify">
  <input type="hidden" name="csrf" value="{html.escape(csrf)}">
  <label for="email">Atlassian account email</label>
  <input id="email" name="email" type="email" required autofocus
         placeholder="you@yourcompany.com">
  <p class="note">Your Atlassian account email — not your Bitbucket username, and not
  the name you gave the token. Bitbucket answers the other two with a bare 401.</p>
  <label for="api_token">API token</label>
  <input id="api_token" name="api_token" type="password" required autocomplete="off">
  <label for="expires_on">Token expires on</label>
  <input id="expires_on" name="expires_on" type="date" required value="{default_expiry}">
  <p class="note">Copy the expiry Atlassian showed you. You are warned a week before it
  lapses, instead of failing mid-review.</p>
  <button type="submit">Check this token</button>
</form>
"""


def _confirm_page(
    signed_in: LoggedIn, candidate: StoredCredential, identity: Identity, csrf: str
) -> str:
    scopes = ", ".join(identity.scopes.granted) or "not reported by Bitbucket"
    body = f"""
<p>Bitbucket says this token belongs to:</p>
<p class="identity">{html.escape(identity.display_name)}</p>
<dl>
  <dt>Email</dt><dd>{html.escape(candidate.email)}</dd>
  <dt>Expires</dt><dd>{candidate.expires_on.isoformat()}</dd>
  <dt>Scopes</dt><dd><code>{html.escape(scopes)}</code></dd>
</dl>
<p class="note">Everything this server posts for you will appear under that name, and you
are accountable for it. If that is someone else's account, close this page.</p>
<form method="post" action="/connect/save">
  <input type="hidden" name="csrf" value="{html.escape(csrf)}">
  <button type="submit">Yes, that is me — save it</button>
</form>
"""
    return page("Is this you?", _greeting(signed_in) + body)


def _done_page(signed_in: LoggedIn, candidate: StoredCredential, identity: Identity) -> str:
    body = f"""
<p class="identity">Connected as {html.escape(identity.display_name)}.</p>
<p>It expires on {candidate.expires_on.isoformat()}; you will be warned a week before.</p>
<p class="note">You can close this page and go back to your review.</p>
"""
    return page("Connected", _greeting(signed_in) + body)


def _forgotten_page(signed_in: LoggedIn) -> str:
    body = f"""
<p>Your Bitbucket credential has been removed from this server.</p>
<p class="note"><strong>The token still exists at Atlassian.</strong> Revoke it at
<a href="{TOKEN_PAGE}" target="_blank" rel="noreferrer">{TOKEN_PAGE}</a> if you are done
with it — this server can only forget it, not destroy it.</p>
"""
    return page("Disconnected", _greeting(signed_in) + body)


__all__ = ["SESSION_COOKIE", "build_connect_app", "cookie_secret"]

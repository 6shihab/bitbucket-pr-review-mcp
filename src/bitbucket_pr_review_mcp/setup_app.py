"""The page a Reviewer fills in once, served by this process while it holds no credential.

ADR-0004 explains why this lives inside the MCP server rather than in a second process,
and what bounds the trade: loopback only, a random port, a one-time token, `Origin` and
`Host` validated, no CORS, and a five-minute life. This module owns the first five; the
listener owns the last two.

The shape of the flow is deliberate. The Reviewer submits, we ask Bitbucket who the
credential belongs to, and we show that display name back **before** storing anything.
No status code catches a token pasted from the wrong Atlassian account; seeing the wrong
person's name does. The credential waits in memory between those two steps rather than
travelling back through the page, so the token crosses the wire exactly once.

Nothing here decides what a good token is — `scopes.py` does — and nothing here talks to
Bitbucket directly; `verify` is injected, which is also what makes this app testable in
process with real routing and real header validation.
"""

from __future__ import annotations

import hmac
import html
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import date, timedelta

import httpx
from loguru import logger
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.requests import Request
from starlette.responses import HTMLResponse, Response
from starlette.routing import Route

from .client import BitbucketError
from .credentials import Credential, CredentialError, StoredCredential
from .keychain import Keychain, KeychainUnavailable
from .scopes import REQUIRED, TOKEN_PAGE
from .verify import Identity

Verify = Callable[[Credential], Awaitable[Identity]]

LOOPBACK_HOSTNAMES = frozenset({"127.0.0.1", "localhost", "::1", "[::1]"})

SETUP_PATH = "/setup"

# Two short strings and a date. Anything larger is not this form.
MAX_BODY_BYTES = 16 * 1024


@dataclass(frozen=True, slots=True)
class HostPolicy:
    """Which `Host` and `Origin` headers this listener will answer to.

    A browser will happily resolve an attacker-controlled name to 127.0.0.1 and then send
    requests to whatever is listening there — DNS rebinding. What it will not do is lie
    about the `Host` it asked for or omit the `Origin` on a cross-site form post, so
    checking both is what makes a loopback listener safe to run.
    """

    port: int | None = None
    hostnames: frozenset[str] = LOOPBACK_HOSTNAMES

    def permits(self, host: str | None, origin: str | None) -> bool:
        return self._permits_host(host) and self._permits_origin(origin)

    def _permits_host(self, host: str | None) -> bool:
        if not host:
            return False
        hostname, _, port = host.rpartition(":")
        if not hostname:  # no port in the header at all
            hostname, port = host, ""
        return self._matches(hostname, port)

    def _permits_origin(self, origin: str | None) -> bool:
        if origin is None:
            return True  # same-origin GETs and form posts may omit it; the Host check stands
        if not origin.startswith("http://"):
            return False
        return self._permits_host(origin.removeprefix("http://"))

    def _matches(self, hostname: str, port: str) -> bool:
        if hostname.strip("[]").lower() not in {name.strip("[]") for name in self.hostnames}:
            return False
        if self.port is None:
            return True
        return port == str(self.port)


@dataclass(slots=True)
class _Session:
    """The listener's whole memory: one verified credential, and whether we are done."""

    pending: StoredCredential | None = None
    identity: Identity | None = None
    finished: bool = False


def build_setup_app(
    *,
    one_time_token: str,
    host_policy: HostPolicy,
    verify: Verify,
    keychain: Keychain,
    on_saved: Callable[[StoredCredential], None],
    today: Callable[[], date] = date.today,
) -> Starlette:
    """The setup application. `verify` and `keychain` are injected; both are real in
    production and swapped in tests, so routing and header checks stay production code."""
    session = _Session()

    async def show_form(request: Request) -> Response:
        return HTMLResponse(_form_page(one_time_token, today()))

    async def check(request: Request) -> Response:
        """Validate, ask Bitbucket who this is, and show the answer back. Stores nothing."""
        form = await request.form()
        try:
            candidate = StoredCredential.of(
                email=str(form.get("email", "")),
                token=str(form.get("api_token", "")),
                expires_on=str(form.get("expires_on", "")),
                today=today(),
            )
        except (CredentialError, ValueError) as exc:
            return _problem(one_time_token, today(), str(exc))

        try:
            identity = await verify(candidate.credential)
        except (BitbucketError, httpx.HTTPError) as exc:
            # A refused credential and an unreachable Bitbucket both belong on the form.
            # Anything else is a bug and should surface as one rather than as advice.
            logger.warning("Setup verification failed for {}: {}", candidate.email, exc)
            return _problem(one_time_token, today(), str(exc))

        if not identity.scopes.acceptable:
            return _problem(one_time_token, today(), identity.scopes.refusal())
        if not identity.scopes.complete:
            return _problem(one_time_token, today(), identity.scopes.shortfall())

        session.pending, session.identity = candidate, identity
        return HTMLResponse(_confirm_page(one_time_token, candidate, identity))

    async def save(request: Request) -> Response:
        """Store what was just verified, then shut the whole thing down."""
        await request.form()
        candidate, identity = session.pending, session.identity
        if candidate is None or identity is None:
            return _problem(
                one_time_token,
                today(),
                "Nothing has been verified yet. Fill the form in again.",
            )

        try:
            keychain.save(candidate)
        except KeychainUnavailable as exc:
            return _problem(one_time_token, today(), str(exc), status=500)

        session.pending, session.identity, session.finished = None, None, True
        on_saved(candidate)
        return HTMLResponse(_done_page(candidate, identity))

    class Gate:
        """Every request, on every path, passes here before routing.

        Plain ASGI rather than `BaseHTTPMiddleware` because the gate has to read the form
        to find the one-time token, and the endpoint then has to read the same body
        again: buffering it here and replaying it downstream is the only way both see it.
        """

        def __init__(self, app):
            self.app = app

        async def __call__(self, scope, receive, send):
            if scope["type"] != "http":
                return await self.app(scope, receive, send)

            body = await _drain(receive)
            request = Request(scope, _replay(body))
            refusal = await self._refusal_for(request)
            if refusal is not None:
                return await refusal(scope, _replay(body), send)
            await self.app(scope, _replay(body), send)

        async def _refusal_for(self, request: Request) -> Response | None:
            headers = request.headers
            if not host_policy.permits(headers.get("host"), headers.get("origin")):
                logger.warning(
                    "Refused a setup request claiming host={!r} origin={!r}.",
                    headers.get("host"),
                    headers.get("origin"),
                )
                return _refused("This request did not come from the setup page on this machine.")

            if session.finished:
                return _refused("This setup link has already been used. It works once.")

            presented = request.query_params.get("token")
            if presented is None and request.method == "POST":
                presented = str((await request.form()).get("setup_token") or "")
            if not _same(presented or "", one_time_token):
                return _refused(
                    "This setup link is not valid. Setup links are single-use and expire "
                    "after five minutes; ask the model to try again to get a fresh one."
                )
            return None

    return Starlette(
        routes=[
            Route(SETUP_PATH, show_form, methods=["GET"]),
            Route("/verify", check, methods=["POST"]),
            Route("/save", save, methods=["POST"]),
        ],
        middleware=[Middleware(Gate)],
    )


async def _drain(receive) -> bytes:
    """Read the whole request body so both the gate and the endpoint can see it.

    Bounded, because buffering is the one thing this middleware does before it has
    decided whether the caller is allowed to be here at all.
    """
    body = b""
    while len(body) <= MAX_BODY_BYTES:
        message = await receive()
        if message["type"] != "http.request":
            break
        body += message.get("body", b"")
        if not message.get("more_body", False):
            break
    return body[: MAX_BODY_BYTES + 1]


def _replay(body: bytes):
    delivered = False

    async def receive():
        nonlocal delivered
        if delivered:
            return {"type": "http.disconnect"}
        delivered = True
        return {"type": "http.request", "body": body, "more_body": False}

    return receive


def _same(presented: str, expected: str) -> bool:
    """Constant-time enough for a value that lives five minutes on loopback."""
    return hmac.compare_digest(presented, expected)


def _refused(message: str) -> HTMLResponse:
    return HTMLResponse(_page("Refused", f"<p class='bad'>{html.escape(message)}</p>"), 403)


def _problem(one_time_token: str, today: date, message: str, status: int = 400) -> HTMLResponse:
    body = f"<p class='bad'>{html.escape(message)}</p>" + _form(one_time_token, today)
    return HTMLResponse(_page("Connect Bitbucket", body), status)


def _form_page(one_time_token: str, today: date) -> str:
    return _page("Connect Bitbucket", _intro() + _form(one_time_token, today))


def _intro() -> str:
    scopes = "".join(f"<li><code>{html.escape(scope)}</code></li>" for scope in REQUIRED)
    return f"""
<p>This connects one Atlassian account to the review server on this machine. It runs
here, on your own computer, and shuts itself down as soon as you are done.</p>
<h2>1. Create an API token</h2>
<p>Open <a href="{TOKEN_PAGE}" target="_blank" rel="noreferrer">{TOKEN_PAGE}</a>,
create a token, and tick exactly these scopes:</p>
<ul class="scopes">{scopes}</ul>
<p class="note">Nothing wider. A token that can also write to a repository, administer
one, or run pipelines will be refused on the next screen — this server reads code and
writes pull request comments, and that is all it should ever be able to do.</p>
<h2>2. Paste it here</h2>
"""


def _form(one_time_token: str, today: date) -> str:
    default_expiry = (today + timedelta(days=365)).isoformat()
    return f"""
<form method="post" action="/verify">
  <input type="hidden" name="setup_token" value="{html.escape(one_time_token)}">
  <label for="email">Atlassian account email</label>
  <input id="email" name="email" type="email" required autofocus
         placeholder="you@yourcompany.com">
  <p class="note">Your Atlassian account email — not your Bitbucket username, and not
  the name you gave the token. Bitbucket answers the other two with a bare 401.</p>
  <label for="api_token">API token</label>
  <input id="api_token" name="api_token" type="password" required autocomplete="off">
  <label for="expires_on">Token expires on</label>
  <input id="expires_on" name="expires_on" type="date" required value="{default_expiry}">
  <p class="note">Copy the expiry Atlassian showed you. The server warns you a week
  before it lapses, instead of failing mid-review.</p>
  <button type="submit">Check this token</button>
</form>
"""


def _confirm_page(one_time_token: str, candidate: StoredCredential, identity: Identity) -> str:
    scopes = ", ".join(identity.scopes.granted) or "not reported by Bitbucket"
    body = f"""
<p>Bitbucket says this token belongs to:</p>
<p class="identity">{html.escape(identity.display_name)}</p>
<dl>
  <dt>Email</dt><dd>{html.escape(candidate.email)}</dd>
  <dt>Expires</dt><dd>{candidate.expires_on.isoformat()}</dd>
  <dt>Scopes</dt><dd><code>{html.escape(scopes)}</code></dd>
</dl>
<p class="note">Everything this server posts will appear under that name, and you are
accountable for it. If that is someone else's account, close this page.</p>
<form method="post" action="/save">
  <input type="hidden" name="setup_token" value="{html.escape(one_time_token)}">
  <button type="submit">Yes, that is me — save it</button>
</form>
"""
    return _page("Is this you?", body)


def _done_page(candidate: StoredCredential, identity: Identity) -> str:
    body = f"""
<p class="identity">Connected as {html.escape(identity.display_name)}.</p>
<p>The credential is in this machine's keychain. It expires on
{candidate.expires_on.isoformat()}; you will be warned a week before.</p>
<p class="note">This page can be closed, and this server has stopped listening.</p>
<script>setTimeout(function () {{ window.close() }}, 1500)</script>
"""
    return _page("Connected", body)


def _page(title: str, body: str) -> str:
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)} — Bitbucket review server</title>
<style>
  :root {{ color-scheme: light dark; }}
  body {{ font: 16px/1.55 system-ui, sans-serif; max-width: 34rem; margin: 3rem auto;
         padding: 0 1.25rem; }}
  h1 {{ font-size: 1.35rem; }}
  h2 {{ font-size: 1rem; margin-top: 1.75rem; }}
  label {{ display: block; margin-top: 1rem; font-weight: 600; }}
  input {{ width: 100%; padding: .55rem; font: inherit; box-sizing: border-box; }}
  button {{ margin-top: 1.25rem; padding: .6rem 1.1rem; font: inherit; cursor: pointer; }}
  .note {{ font-size: .85rem; opacity: .75; }}
  .bad {{ padding: .75rem; border-left: 3px solid #c33; background: rgba(204,51,51,.08); }}
  .identity {{ font-size: 1.2rem; font-weight: 600; }}
  .scopes code {{ font-size: .95rem; }}
  dt {{ font-weight: 600; margin-top: .5rem; }}
</style></head>
<body><h1>{html.escape(title)}</h1>{body}</body></html>
"""

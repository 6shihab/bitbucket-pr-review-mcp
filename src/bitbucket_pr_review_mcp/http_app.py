"""The shared server's front door: MCP over HTTP, with a person attached to every call.

Three things live here and nothing else does. The metadata document that tells Claude
where to send people to authorise. The 401 that starts that conversation. And the
middleware that turns an `Authorization` header into "this call is Alice's", which is the
sentence the entire shared deployment is built to be able to say.

**Stateless, deliberately.** Streamable HTTP can keep a long-lived session task and feed
later requests into it; then the work happens in the session's context rather than the
request's, and "whose call is this?" is answered by whoever opened the session. Stateless
mode handles each request on its own, so the person bound at the door is the person the
tool runs as. It also means no session affinity, which is the difference between running
one of these and running two.

**Pure ASGI, deliberately.** Starlette's `BaseHTTPMiddleware` consumes the request body
before the app sees it, which this project already learned the hard way on the setup
listener. Nothing here reads the body at all.
"""

from __future__ import annotations

from collections.abc import Callable

import httpx
from loguru import logger
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from .discovery import ProtectedResource
from .gate import CredentialGate, Setup
from .server import build_server
from .sessions import PerPerson, Session, acting_as
from .settings import Allowlist, Settings
from .tokens import InsufficientScope, IssuerUnreachable, TokenRejected, TokenVerifier
from .vault import CredentialVault

MCP_PATH = "/mcp"


class RequireToken:
    """Every request past this point belongs to somebody, or it does not pass.

    The refusals are shaped the way Claude needs them: a 401 carrying
    `WWW-Authenticate` with a `resource_metadata` pointer is how a connector discovers
    where to send a person to authorise. Answering a missing credential with a polite
    200 — or with a tool-level error — means the connector never finds out, and the only
    symptom anybody sees is "Couldn't reach the MCP server".
    """

    def __init__(self, app, verifier: TokenVerifier, resource: ProtectedResource) -> None:
        self.app = app
        self.verifier = verifier
        self.resource = resource

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        presented = _header(scope, b"authorization")

        try:
            caller = await self.verifier.verify(presented)
        except InsufficientScope as exc:
            return await _refuse(
                send, 403, self.resource.challenge(error="insufficient_scope"), str(exc)
            )
        except TokenRejected as exc:
            return await _refuse(send, 401, self.resource.challenge(), str(exc))
        except IssuerUnreachable as exc:
            # Not the caller's fault, and not a 401: telling a client to reauthorise
            # when the authorization server is down produces a login loop.
            logger.error("Cannot reach the authorization server: {}", exc)
            return await _refuse(send, 503, None, "The authorization server is unreachable.")

        with acting_as(caller.person):
            await self.app(scope, receive, send)


def build_http_app(
    settings: Settings,
    allowlist: Allowlist,
    vault: CredentialVault,
    verifier: TokenVerifier,
    resource: ProtectedResource,
    setup: Setup,
    http_factory: Callable[[], httpx.AsyncClient] | None = None,
) -> Starlette:
    """The whole shared server as one ASGI application."""

    def make(person: str) -> Session:
        return Session.around(CredentialGate(vault.for_person(person), setup))

    sessions = PerPerson(make)
    mcp = build_server(settings, allowlist, sessions, http_factory)

    async def metadata(request: Request) -> JSONResponse:
        return JSONResponse(
            resource.document(),
            headers={"cache-control": "public, max-age=3600"},
        )

    # Both forms. Claude probes the path-suffixed one first and falls back to the bare
    # one, and serving only the form that happens to match today is how discovery breaks
    # the first time somebody adds a path.
    paths = {resource.metadata_path(), "/.well-known/oauth-protected-resource"}

    inner = mcp.http_app(path=MCP_PATH, transport="http", stateless_http=True)
    app = Starlette(
        routes=[Route(path, metadata, methods=["GET"]) for path in sorted(paths)],
        lifespan=inner.lifespan,
    )
    app.mount("", RequireToken(inner, verifier, resource))
    return app


def _header(scope, wanted: bytes) -> str | None:
    for name, value in scope.get("headers", []):
        if name.lower() == wanted:
            return value.decode("latin-1")
    return None


async def _refuse(send, status: int, challenge: str | None, because: str) -> None:
    headers = [(b"content-type", b"application/json")]
    if challenge:
        headers.append((b"www-authenticate", challenge.encode("latin-1")))

    body = JSONResponse({"error": because}).body
    await send({"type": "http.response.start", "status": status, "headers": headers})
    await send({"type": "http.response.body", "body": body})


__all__ = ["MCP_PATH", "RequireToken", "build_http_app"]

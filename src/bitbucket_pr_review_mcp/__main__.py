"""Entrypoint: ``uv run bb-pr-mcp``.

Speaks MCP over stdio, so stdout belongs to the protocol and nothing else. Every log
line goes to stderr; a stray print here corrupts the stream in a way that is miserable
to debug from the client side.

Startup does three things a Reviewer would otherwise discover the hard way: it warns when
the stored token is nearly out of time, it refuses to run at all when the token grants
more than reading repositories and writing to pull requests, and — when there is no
usable credential — it opens the setup page and says so, rather than starting a server
whose every tool will fail.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
from collections.abc import Callable

import httpx
from loguru import logger

from . import __version__
from .client import BitbucketError, Unauthorized, build_http_client
from .credentials import Credential, CredentialError, StoredCredential
from .environment import EnvironmentStore, SetupUnavailable
from .gate import CredentialGate
from .keychain import Keychain
from .scopes import TOKEN_PAGE
from .server import build_server
from .settings import Allowlist, ConfigError, Settings, load_allowlist
from .setup_listener import SetupListener
from .verify import Identity, verify_credential

SETUP_WAIT_SECONDS = 310.0


def configure_logging(level: str) -> None:
    """Send logs to stderr, never stdout."""
    logger.remove()
    logger.add(
        sys.stderr,
        level=level.upper(),
        format=(
            "<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | "
            "<cyan>{name}</cyan> - <level>{message}</level>"
        ),
    )


def _load() -> tuple[Settings, Allowlist]:
    """Load configuration, or exit with a message a human can act on."""
    settings = Settings()
    configure_logging(settings.log_level)
    try:
        allowlist = load_allowlist(settings.repositories_file)
    except ConfigError as exc:
        logger.error("Cannot start:\n{}", exc)
        raise SystemExit(2) from exc
    return settings, allowlist


def _build_gate(
    settings: Settings,
    allowlist: Allowlist,
    http_factory: Callable[[], httpx.AsyncClient] | None = None,
) -> CredentialGate:
    """The credential, the keychain it lives in, and the setup page that fills it."""
    make_http = http_factory or (lambda: build_http_client(settings.request_timeout_seconds))

    async def verify(credential: Credential) -> Identity:
        return await verify_credential(credential, allowlist, make_http)

    supplied = EnvironmentStore.configured()
    if supplied is not None:
        supplied.announce()
        return CredentialGate(supplied, SetupUnavailable())

    keychain = Keychain()
    return CredentialGate(keychain, SetupListener(keychain=keychain, verify=verify))


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="bb-pr-mcp",
        description="MCP server for reviewing Bitbucket Cloud pull requests.",
    )
    parser.add_argument("--version", action="version", version=f"bb-pr-mcp {__version__}")
    parser.add_argument(
        "--check",
        action="store_true",
        help="Validate configuration and credential, then exit.",
    )
    parser.add_argument(
        "--setup",
        action="store_true",
        help="Open the credential setup page in this terminal's browser, then exit.",
    )
    parser.add_argument(
        "--forget",
        action="store_true",
        help="Remove the stored credential from this device's keychain, then exit.",
    )
    parser.add_argument(
        "--http",
        action="store_true",
        help="Serve the shared deployment over HTTP instead of stdio (several people).",
    )
    parser.add_argument("--host", default="127.0.0.1", help="Address to bind with --http.")
    parser.add_argument("--port", type=int, default=8000, help="Port to bind with --http.")
    args = parser.parse_args()

    settings, allowlist = _load()

    if args.http:
        raise SystemExit(_run_http(settings, allowlist, args.host, args.port))
    gate = _build_gate(settings, allowlist)

    if args.forget:
        raise SystemExit(_run_forget(gate))

    if args.setup:
        raise SystemExit(_run_setup(gate))

    if args.check:
        raise SystemExit(asyncio.run(_run_check(settings, allowlist, gate)))

    _startup_checks(settings, allowlist, gate)
    logger.info(
        "Serving {} allowlisted repositor{} over stdio.",
        len(allowlist.names()),
        "y" if len(allowlist.names()) == 1 else "ies",
    )
    try:
        build_server(settings, allowlist, gate).run()
    finally:
        gate.close()


def _startup_checks(
    settings: Settings,
    allowlist: Allowlist,
    gate: CredentialGate,
    http_factory: Callable[[], httpx.AsyncClient] | None = None,
) -> None:
    """Say at startup what a Reviewer would otherwise learn from a failing tool call.

    One of these is fatal. A token that can do more than read repositories and write to
    pull requests is not a token this server will hold, and starting anyway would leave
    that decision to whoever reads the logs — which is nobody (ADR-0002).
    """
    try:
        warning = gate.expiry_warning()
        if warning:
            logger.warning(warning)
        credential = gate.current()
    except CredentialError as exc:
        # Not fatal: the server starts, and every tool answers with this same message —
        # including "there is no keychain here", which is how this looks in a container.
        logger.warning("{}", exc)
        return

    make_http = http_factory or (lambda: build_http_client(settings.request_timeout_seconds))
    try:
        identity = asyncio.run(verify_credential(credential, allowlist, make_http))
    except Unauthorized as exc:
        gate.report_unauthorized()
        logger.warning("{}", exc)
        return
    except (BitbucketError, httpx.HTTPError) as exc:
        # Bitbucket being unreachable is not evidence about the token. Tools will say so.
        logger.warning("Could not check the credential with Bitbucket: {}", exc)
        return

    if not identity.scopes.acceptable:
        logger.error("Refusing to start. {}", identity.scopes.refusal())
        raise SystemExit(2)
    if identity.scopes.verified and not identity.scopes.complete:
        logger.warning("{}", identity.scopes.shortfall())

    logger.info("Posting as {} ({}).", identity.display_name, credential.email)


async def _run_check(
    settings: Settings,
    allowlist: Allowlist,
    gate: CredentialGate,
    http_factory: Callable[[], httpx.AsyncClient] | None = None,
) -> int:
    """Prove the configuration and the credential actually work. Returns a shell code."""
    logger.info("Allowlisted repositories: {}", ", ".join(allowlist.names()))

    try:
        warning = gate.expiry_warning()
        if warning:
            logger.warning(warning)
        credential = gate.current()
    except CredentialError as exc:
        logger.error("{}", exc)
        return 1

    make_http = http_factory or (lambda: build_http_client(settings.request_timeout_seconds))
    try:
        identity = await verify_credential(credential, allowlist, make_http)
    except Unauthorized as exc:
        gate.report_unauthorized()
        logger.error("Credential unusable: {}", exc)
        return 1
    except BitbucketError as exc:
        logger.error("Credential unusable: {}", exc)
        return 1

    if not identity.scopes.acceptable:
        logger.error("{}", identity.scopes.refusal())
        return 2
    if identity.scopes.verified and not identity.scopes.complete:
        logger.error("{}", identity.scopes.shortfall())
        return 2
    if not identity.scopes.verified:
        logger.warning(
            "Bitbucket did not report this token's scopes, so they could not be checked."
        )

    logger.info("Authenticated as {} ({}).", identity.display_name, credential.email)
    return 0


def _run_http(settings: Settings, allowlist: Allowlist, host: str, port: int) -> int:
    """The shared deployment: several people, one server, a token per request.

    Nothing here degrades into something weaker. A missing vault key, an undescribable
    public URL or an absent issuer all stop the process, because each of them is the
    difference between holding other people's credentials safely and appearing to.
    """
    import uvicorn

    from .discovery import DiscoveryError, ProtectedResource
    from .http_app import CONNECT_PATH, build_http_app
    from .tokens import SigningKeys, TokenVerifier
    from .vault import CredentialVault, VaultKey

    try:
        key = VaultKey.required()
        resource = ProtectedResource.of(settings.public_url, settings.oidc_issuer)
    except (CredentialError, DiscoveryError) as exc:
        logger.error("Cannot start the shared server: {}", exc)
        return 2

    vault = CredentialVault.at(settings.vault_file, key)
    verifier = TokenVerifier(
        resource=resource,
        keys=SigningKeys(
            resource.authorization_servers[0],
            build_http_client(settings.request_timeout_seconds),
        ),
    )

    connect_url = resource.resource.rsplit("/", 1)[0] + CONNECT_PATH
    connect, setup = _connect_pages(settings, allowlist, vault, resource, connect_url)

    logger.info("Serving MCP over HTTP at {}.", resource.resource)
    logger.info("Tokens are accepted from {}.", resource.authorization_servers[0])
    logger.info("{} person(s) have connected Bitbucket so far.", len(vault.enrolled()))

    app = build_http_app(
        settings, allowlist, vault, verifier, resource, setup, connect_factory=connect
    )
    try:
        uvicorn.run(app, host=host, port=port, log_config=None)
    finally:
        vault.close()
    return 0


def _connect_pages(settings, allowlist, vault, resource, connect_url):
    """The page that collects an Atlassian API token, behind a Keycloak sign-in.

    Without a client secret there is no way to make a browser prove who it is, and a page
    that collects credentials without knowing who is filling it in is the confused deputy
    ticket 13 exists to prevent. So it is not served at all, and callers are told why
    rather than being sent to something that cannot work.
    """
    from .connect_app import ConnectHere, build_connect_app, cookie_secret
    from .oidc import RelyingParty
    from .tokens import SigningKeys
    from .vault import VaultKey

    if not settings.oidc_client_secret:
        logger.warning(
            "No {}: the page for connecting a Bitbucket account is not being served.",
            "BB_MCP_OIDC_CLIENT_SECRET",
        )
        return None, _ConnectPageUnavailable()

    async def verify(credential: Credential) -> Identity:
        return await verify_credential(
            credential, allowlist, lambda: build_http_client(settings.request_timeout_seconds)
        )

    issuer = resource.authorization_servers[0]
    party = RelyingParty(
        issuer=issuer,
        client_id=settings.oidc_client_id,
        client_secret=settings.oidc_client_secret,
        redirect_uri=f"{connect_url}/callback",
        keys=SigningKeys(issuer, build_http_client(settings.request_timeout_seconds)),
        http=build_http_client(settings.request_timeout_seconds),
    )
    def connect(forget):
        """`forget` invalidates one person's cached session. The page needs it: it writes
        straight to the vault, and a session that has already read a credential would go
        on using one that has just been replaced or disconnected."""
        return build_connect_app(
            party=party,
            vault=vault,
            verify=verify,
            secret=cookie_secret(VaultKey.required().material),
            public_url=resource.resource,
            on_change=forget,
        )

    logger.info("People connect their Bitbucket account at {}.", connect_url)
    return connect, ConnectHere(connect_url)


class _ConnectPageUnavailable:
    """Stands in for the connect page when this deployment cannot serve one."""

    def start(self, on_saved=None) -> str:
        raise CredentialError(
            "You are authenticated, but this server has no Bitbucket credential for you "
            "and cannot offer the page that collects one: it has no OIDC client secret, "
            "so it cannot ask a browser to sign in. Whoever runs this server needs to "
            "set BB_MCP_OIDC_CLIENT_SECRET."
        )

    def stop(self) -> None:
        return None


def _run_forget(gate: CredentialGate) -> int:
    """Remove the credential from this device. Deliberately a command, not a tool.

    The token also has a life outside this machine: forgetting it here stops this server
    using it and nothing else, so the message says where to actually revoke it.
    """
    try:
        stored = gate.stored()
        gate.forget()
    except CredentialError as exc:
        logger.error("{}", exc)
        return 2

    if stored is None:
        logger.info("There was no credential stored on this device.")
    else:
        logger.info(
            "Removed the credential for {} from this device. It still exists at "
            "Atlassian: revoke it at {} if it should stop working everywhere.",
            stored.email,
            TOKEN_PAGE,
        )
    logger.info("Run `bb-pr-mcp --setup` to connect an account again.")
    return 0


def _run_setup(gate: CredentialGate) -> int:
    """Open the setup page deliberately, and wait for a credential to be entered.

    Entered, not merely present. This command is how a Reviewer replaces a token that
    still works — after an expiry warning, or because the old one leaked — and a version
    that stopped as soon as it found *a* credential would close the page before they had
    a chance to use it.
    """
    entered: list[StoredCredential] = []

    try:
        replacing = gate.stored() is not None
        url = gate.open_setup(entered.append)
    except CredentialError as exc:
        # There is nowhere to save a credential — a container, or a broken keychain.
        # The message says where this can be fixed instead; a traceback would not.
        logger.error("{}", exc)
        return 2

    logger.info(
        "Open this page to {} Bitbucket:\n    {}",
        "replace the credential for" if replacing else "connect",
        url,
    )
    if replacing:
        logger.info("The credential already stored keeps working until you save a new one.")

    deadline = time.monotonic() + SETUP_WAIT_SECONDS
    while time.monotonic() < deadline and not entered:
        time.sleep(0.25)

    gate.close()
    if entered:
        logger.info("Connected as {}. Start your MCP client as usual.", entered[0].email)
        return 0

    logger.error("Setup was not completed. Run this again for a fresh link.")
    return 1


if __name__ == "__main__":
    main()

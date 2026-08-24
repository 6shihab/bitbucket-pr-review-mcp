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
from .gate import CredentialGate
from .keychain import Keychain
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
    args = parser.parse_args()

    settings, allowlist = _load()
    gate = _build_gate(settings, allowlist)

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
    warning = gate.expiry_warning()
    if warning:
        logger.warning(warning)

    try:
        credential = gate.current()
    except CredentialError as exc:
        # Not fatal: the server starts, and every tool answers with this same message.
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

    warning = gate.expiry_warning()
    if warning:
        logger.warning(warning)

    try:
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


def _run_setup(gate: CredentialGate) -> int:
    """Open the setup page deliberately, and wait for a credential to be entered.

    Entered, not merely present. This command is how a Reviewer replaces a token that
    still works — after an expiry warning, or because the old one leaked — and a version
    that stopped as soon as it found *a* credential would close the page before they had
    a chance to use it.
    """
    entered: list[StoredCredential] = []
    replacing = gate.stored() is not None

    logger.info(
        "Open this page to {} Bitbucket:\n    {}",
        "replace the credential for" if replacing else "connect",
        gate.open_setup(entered.append),
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

"""Entrypoint: ``uv run bb-pr-mcp``.

Speaks MCP over stdio, so stdout belongs to the protocol and nothing else. Every log
line goes to stderr; a stray print here corrupts the stream in a way that is miserable
to debug from the client side.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections.abc import Callable

import httpx
from loguru import logger

from . import __version__
from .client import BitbucketClient, BitbucketError, build_http_client
from .credentials import Credential, CredentialError, from_environment
from .server import build_server
from .settings import Allowlist, ConfigError, Settings, load_allowlist


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


def _load() -> tuple[Settings, Allowlist, Credential]:
    """Load configuration, or exit with a message a human can act on."""
    settings = Settings()
    configure_logging(settings.log_level)
    try:
        allowlist = load_allowlist(settings.repositories_file)
        credential = from_environment()
    except (ConfigError, CredentialError) as exc:
        logger.error("Cannot start:\n{}", exc)
        raise SystemExit(2) from exc
    return settings, allowlist, credential


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
    args = parser.parse_args()

    settings, allowlist, credential = _load()

    if args.check:
        raise SystemExit(asyncio.run(_run_check(settings, allowlist, credential)))

    logger.info(
        "Serving {} allowlisted repositor{} over stdio.",
        len(allowlist.names()),
        "y" if len(allowlist.names()) == 1 else "ies",
    )
    build_server(settings, allowlist, credential).run()


async def _run_check(
    settings: Settings,
    allowlist: Allowlist,
    credential: Credential,
    http_factory: Callable[[], httpx.AsyncClient] | None = None,
) -> int:
    """Prove the configuration and the credential actually work. Returns a shell code."""
    logger.info("Allowlisted repositories: {}", ", ".join(allowlist.names()))

    make_http = http_factory or (lambda: build_http_client(settings.request_timeout_seconds))
    client = BitbucketClient(
        http=make_http(),
        allowlist=allowlist,
        credential=credential,
    )
    try:
        user = await client.get_json("/2.0/user")
    except BitbucketError as exc:
        logger.error("Credential unusable: {}", exc)
        return 1
    finally:
        await client.aclose()

    logger.info("Authenticated as {} ({}).", user.get("display_name", "?"), credential.email)
    return 0


if __name__ == "__main__":
    main()

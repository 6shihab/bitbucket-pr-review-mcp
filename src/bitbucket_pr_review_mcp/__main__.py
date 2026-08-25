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
from datetime import date

import httpx
from loguru import logger

from . import __version__
from .client import BitbucketError, Unauthorized, build_http_client
from .credentials import Credential, CredentialError, StoredCredential
from .environment import EnvironmentStore, SetupUnavailable
from .gate import EXPIRY_WARNING_DAYS, CredentialGate
from .keychain import Keychain
from .scopes import TOKEN_PAGE
from .server import build_server
from .settings import Allowlist, ConfigError, Settings, load_allowlist
from .setup_listener import SetupListener
from .verify import Identity, verify_credential

SETUP_WAIT_SECONDS = 310.0


def configure_logging(level: str, structured: bool = False) -> None:
    """Send logs to stderr, never stdout.

    `structured` writes one JSON object per line, for a shared server whose logs are
    shipped somewhere central. What is *in* those lines is the part that matters, and it
    is asserted rather than assumed: `tests/test_logs_are_safe_to_ship.py` runs the server
    through its failure paths and goes looking for every secret it handles.
    """
    logger.remove()
    if structured:
        logger.add(sys.stderr, level=level.upper(), serialize=True)
        return

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
    configure_logging(settings.log_level, settings.log_json)
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
    parser.add_argument(
        "--who",
        action="store_true",
        help="List who has connected Bitbucket to the shared server, then exit.",
    )
    parser.add_argument(
        "--revoke",
        metavar="EMAIL_OR_ID",
        help="Delete one person's stored Bitbucket credential from the shared server.",
    )
    parser.add_argument(
        "--health",
        action="store_true",
        help="Report whether the shared deployment is fit to run, then exit.",
    )
    parser.add_argument(
        "--rotate-key",
        metavar="KEYFILE",
        help="Re-seal every stored credential under the key in KEYFILE, then exit.",
    )
    args = parser.parse_args()

    settings, allowlist = _load()

    if args.health:
        raise SystemExit(_run_health(settings, allowlist))

    if args.rotate_key:
        raise SystemExit(_run_rotate(settings, args.rotate_key))

    if args.who:
        raise SystemExit(_run_who(settings))

    if args.revoke:
        raise SystemExit(_run_revoke(settings, args.revoke))

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


def _announce_whole_workspaces(allowlist: Allowlist) -> None:
    """Say out loud that the boundary is a workspace rather than a list of repositories.

    `workspace/*` is the widest entry the allowlist can hold, and the part that does not
    show up anywhere is that it covers repositories created after it was written. That is
    the whole point of it and also the thing nobody remembers, so it is said at every
    startup rather than left in the file for somebody to notice.
    """
    whole = allowlist.whole_workspaces()
    if not whole:
        return

    logger.warning(
        "Allowlist admits {} entire — every repository in {}, including ones created "
        "after this was configured. Bitbucket sells no permission that separates "
        "commenting from merging (ADR-0002), so branch restrictions are what bounds this.",
        ", ".join(f"{workspace}/*" for workspace in whole),
        "them" if len(whole) > 1 else "it",
    )


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
    _announce_whole_workspaces(allowlist)

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

    # Before anything that can refuse to start. This path does not run `_startup_checks`,
    # and it is the deployment where the allowlist bounds more than one person's access.
    _announce_whole_workspaces(allowlist)

    try:
        key = VaultKey.required()
        resource = ProtectedResource.of(settings.public_url, settings.oidc_issuer)
        # Opening the store belongs in here with the others. It is the same kind of
        # refusal — a deployment that cannot hold credentials safely — and left outside,
        # its message arrived wrapped in a traceback, which reads as a crash rather than
        # as the sentence it is.
        vault = CredentialVault.at(settings.vault_file, key)
    except (CredentialError, DiscoveryError) as exc:
        logger.error("Cannot start the shared server: {}", exc)
        return 2

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


def _open_vault(settings: Settings):
    """The shared server's credential store, or a message saying why not."""
    from .vault import CredentialVault, VaultKey

    try:
        key = VaultKey.required()
    except CredentialError as exc:
        logger.error("{}", exc)
        return None
    return CredentialVault.at(settings.vault_file, key)


def _run_who(settings: Settings) -> int:
    """Who has connected Bitbucket, and when their token runs out. No tokens.

    An operator needs this to offboard somebody and to see an expiry before it becomes a
    401 in the middle of a review. It reads the vault, which means it decrypts — and then
    prints everything except the one field worth decrypting for.
    """
    vault = _open_vault(settings)
    if vault is None:
        return 2

    try:
        enrolled = vault.enrolled()
    finally:
        vault.close()

    if not enrolled:
        logger.info("Nobody has connected a Bitbucket account to this server.")
        return 0

    logger.info(
        "{} {} connected Bitbucket:",
        len(enrolled),
        "person has" if len(enrolled) == 1 else "people have",
    )
    today = date.today()
    for person in enrolled:
        if not person.readable:
            logger.warning(
                "  {}  UNREADABLE — this row will not decrypt with the current key",
                person.person[:12],
            )
            continue
        days = (person.expires_on - today).days
        note = "expired" if days < 0 else f"{days} days left"
        logger.info(
            "  {}  {}  connected {}  token expires {} ({})",
            person.person[:12],
            person.email,
            person.connected_at.strftime("%Y-%m-%d"),
            person.expires_on.isoformat(),
            note,
        )
    return 0


def _run_revoke(settings: Settings, wanted: str) -> int:
    """Delete one person's stored credential, and say plainly what is still true.

    Three different places hold something after somebody leaves, and this command owns
    exactly one of them. Pretending otherwise is how an offboarding checklist gets ticked
    while the person still has access.
    """
    vault = _open_vault(settings)
    if vault is None:
        return 2

    try:
        matches = _matching(vault.enrolled(), wanted)
        if not matches:
            logger.error(
                "Nobody here matches {!r}. Run --who to see who has connected.", wanted
            )
            return 1
        if len(matches) > 1:
            logger.error("{!r} matches {} people:", wanted, len(matches))
            for person in matches:
                logger.error("  {}  {}", person.person[:12], person.email or "unreadable")
            logger.error("Name one of them exactly.")
            return 2

        person = matches[0]
        vault.clear(person.person)
    finally:
        vault.close()

    logger.info(
        "Removed the stored Bitbucket credential for {}.", person.email or person.person[:12]
    )
    logger.warning(
        "That is all this command can do. Two things are still true:\n"
        "  * They can still sign in here, and connect a new token. Disable their "
        "account in Keycloak to stop that.\n"
        "  * Their Atlassian API token still exists and still works everywhere else. "
        f"Only they, or an Atlassian admin, can revoke it at {TOKEN_PAGE}."
    )
    return 0


def _matching(enrolled, wanted: str) -> list:
    """Find somebody by email, or by enough of the opaque id to be unambiguous."""
    needle = wanted.strip().lower()
    exact = [person for person in enrolled if person.person == needle]
    if exact:
        return exact

    return [
        person
        for person in enrolled
        if (person.email or "").lower() == needle
        or (len(needle) >= 8 and person.person.startswith(needle))
    ]


def _run_health(
    settings: Settings,
    allowlist: Allowlist,
    http_factory: Callable[[], httpx.AsyncClient] | None = None,
) -> int:
    """Is this deployment fit to hold other people's credentials?

    Exit status is the point: 0 healthy, 1 something is wrong that an operator should
    look at, 2 this will not start at all. Anything a monitoring system can act on.
    """
    from .discovery import DiscoveryError, ProtectedResource
    from .tokens import IssuerUnreachable, SigningKeys
    from .vault import CredentialVault, VaultError, VaultKey

    concerns: list[str] = []

    # "entries" rather than "repositories": one of them can be a whole workspace, and
    # reporting `jantrik/*` as "1 repositories" would understate it by however many
    # repositories that workspace holds.
    logger.info(
        "Allowlist: {} entries — {}",
        len(allowlist.names()),
        ", ".join(allowlist.names()),
    )
    _announce_whole_workspaces(allowlist)

    try:
        key = VaultKey.required()
    except CredentialError as exc:
        logger.error("Vault key: absent. {}", exc)
        return 2
    logger.info("Vault key: supplied, and not from the database.")

    try:
        resource = ProtectedResource.of(settings.public_url, settings.oidc_issuer)
    except DiscoveryError as exc:
        logger.error("Address: {}", exc)
        return 2

    logger.info("Serving as: {}", resource.resource)
    logger.info("Trusting tokens from: {}", resource.authorization_servers[0])

    if not resource.resource.startswith("https://"):
        concerns.append(
            "Serving over plain http. Anthropic connects to this address over the "
            "internet, and an access token on the wire in clear is the whole deployment."
        )

    try:
        vault = CredentialVault.at(settings.vault_file, key)
    except (VaultError, OSError) as exc:
        logger.error("Credential store: unreachable at {} ({}).", settings.vault_file, exc)
        return 1

    try:
        enrolled = vault.enrolled()
    finally:
        vault.close()

    unreadable = [person for person in enrolled if not person.readable]
    logger.info(
        "Credential store: {} at {}, {} connected.",
        "readable",
        settings.vault_file,
        len(enrolled),
    )
    if unreadable:
        concerns.append(
            f"{len(unreadable)} stored credential(s) will not decrypt with this key. "
            "Either the key is not the one they were sealed under, or those rows are damaged."
        )

    today = date.today()
    lapsing = [
        person
        for person in enrolled
        if person.readable and (person.expires_on - today).days <= EXPIRY_WARNING_DAYS
    ]
    for person in lapsing:
        days = (person.expires_on - today).days
        logger.warning(
            "{} token {} on {}.",
            person.email,
            "expired" if days < 0 else f"expires in {days} days",
            person.expires_on.isoformat(),
        )

    issuer = resource.authorization_servers[0]
    try:
        make_http = http_factory or (
            lambda: build_http_client(settings.request_timeout_seconds)
        )
        keys = SigningKeys(issuer, make_http())
        metadata = asyncio.run(keys.metadata())
    except IssuerUnreachable as exc:
        logger.error("Authorization server: unreachable. {}", exc)
        return 1
    logger.info(
        "Authorization server: reachable, PKCE {}.",
        ", ".join(metadata.get("code_challenge_methods_supported", [])) or "not advertised",
    )
    if "S256" not in metadata.get("code_challenge_methods_supported", []):
        concerns.append("The authorization server does not advertise PKCE S256.")

    if settings.oidc_client_secret:
        connect_at = resource.resource.rsplit("/", 1)[0] + "/connect"
        logger.info("Connect page: available at {}.", connect_at)
    else:
        concerns.append(
            "No OIDC client secret, so nobody can connect a Bitbucket account: the page "
            "that collects one cannot ask a browser who it is."
        )

    for concern in concerns:
        logger.warning("{}", concern)

    if concerns:
        logger.warning("Healthy enough to run, with {} thing(s) to look at.", len(concerns))
        return 1

    logger.info("Healthy.")
    return 0


def _run_rotate(settings: Settings, replacement: str) -> int:
    """Re-seal every stored credential under a new key. Nobody re-enrols.

    A suspected key exposure should be an operation somebody can perform on a Tuesday
    rather than an onboarding exercise for the whole team.
    """
    from pathlib import Path as _Path

    from .vault import VaultError, VaultKey

    try:
        new_key = VaultKey.parse(_Path(replacement).read_text(encoding="utf-8"))
    except OSError as exc:
        logger.error("Cannot read the new key from {}: {}", replacement, exc.strerror)
        return 2
    except VaultError as exc:
        logger.error("{} does not hold a vault key. {}", replacement, exc)
        return 2

    vault = _open_vault(settings)
    if vault is None:
        return 2

    try:
        resealed = vault.rotate(new_key)
    except VaultError as exc:
        logger.error("Nothing was rotated. {}", exc)
        return 1
    finally:
        vault.close()

    logger.info("Re-sealed {} stored credential(s) under the new key.", resealed)
    logger.warning("The server is now readable ONLY with the new key.")
    logger.warning("  1. Point BB_MCP_VAULT_KEY_FILE at {} and restart.", replacement)
    logger.warning("  2. Back the new key up somewhere the store's backups do not reach.")
    logger.warning("  3. Destroy the old key only once step 2 is done and verified.")
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

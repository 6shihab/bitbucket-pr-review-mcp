"""What every tool asks before it does anything: may I have the credential?

This is the single place that decides the answer, and — more importantly — the single
place that decides when the setup listener opens. ADR-0004 allows exactly three reasons,
all of them observed facts: there is no credential, the stored one is past the expiry
the Reviewer gave it, or Bitbucket has just rejected it with a 401. Nothing a Caller
sends can appear in that list, which is why `SetupRequired` is raised by this module and
never constructed from tool input.

A rejected credential is not deleted. A 401 can be a revoked token, a typo made
elsewhere, or Atlassian having a bad afternoon; destroying the Reviewer's stored token in
response to any of those would turn a five-minute annoyance into a re-issue. It is marked
rejected in memory instead, which reopens setup now and forgets the accusation on restart.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date
from typing import Protocol

from loguru import logger

from .credentials import Credential, CredentialError, StoredCredential
from .keychain import Keychain

RENEW_COMMAND = "uv run bb-pr-mcp --setup"

EXPIRY_WARNING_DAYS = 7


class Setup(Protocol):
    """The setup listener, as far as the gate is concerned."""

    def start(self, on_saved: Callable[[StoredCredential], None]) -> str: ...

    def stop(self) -> None: ...


class SetupRequired(CredentialError):
    """No usable credential. The message names the URL that fixes it."""

    def __init__(self, url: str, because: str) -> None:
        super().__init__(
            f"{because} Open this page to connect Bitbucket, then run this tool again:\n"
            f"    {url}\n"
            "The link works once and expires in five minutes. It is served by this "
            "server on your own machine."
        )
        self.url = url


class CredentialGate:
    """Holds the credential, and opens setup on the three facts that justify it."""

    def __init__(
        self,
        keychain: Keychain,
        setup: Setup,
        today: Callable[[], date] = date.today,
    ) -> None:
        self._keychain = keychain
        self._setup = setup
        self._today = today
        self._rejected = False
        self._held: StoredCredential | None = None
        self._on_new_credential: list[Callable[[], None]] = []

    def current(self) -> Credential:
        """The credential to use right now, or an error naming the setup URL."""
        stored = self.stored()

        if stored is None:
            raise self._open("This server has no Bitbucket credential yet.")
        if self._rejected:
            raise self._open("Bitbucket rejected the stored credential.")
        if stored.is_expired(self._today()):
            raise self._open(
                f"The stored credential expired on {stored.expires_on.isoformat()}."
            )
        return stored.credential

    def when_credential_changes(self, forget: Callable[[], None]) -> None:
        """Anything cached per-credential registers here. A new credential is a new
        account, and a stale answer to "is this comment ours?" is worse than no answer."""
        self._on_new_credential.append(forget)

    def stored(self) -> StoredCredential | None:
        """What is in the keychain, expired or not. Raises if the keychain is unreachable.

        Read once and then held: on macOS every keychain read is a potential prompt, and
        a tool call that asks the Reviewer to authorise something is exactly the habit
        this server should not be building. Only a successful read is cached, so the
        no-credential-yet loop keeps looking.
        """
        if self._held is None:
            self._held = self._keychain.load()
        return self._held

    def accept_saved(self, stored: StoredCredential) -> None:
        """Called by the listener when the Reviewer completes setup."""
        self._rejected, self._held = False, stored
        for forget in self._on_new_credential:
            forget()
        logger.info("Credential accepted for {}.", stored.credential.email)

    def report_unauthorized(self) -> None:
        """Bitbucket answered 401. Reopen setup; leave the stored credential alone."""
        self._rejected = True
        logger.warning("Bitbucket rejected the credential; reopening setup.")

    def expiry_warning(self) -> str | None:
        """A warning to log at startup when the token is nearly out of time."""
        stored = self.stored()
        if stored is None:
            return None

        days = stored.days_left(self._today())
        if days < 0:
            return (
                f"The stored credential expired on {stored.expires_on.isoformat()}. "
                f"Renew it with: {RENEW_COMMAND}"
            )
        if days <= EXPIRY_WARNING_DAYS:
            return (
                f"The stored credential expires in {days} day{'' if days == 1 else 's'} "
                f"(on {stored.expires_on.isoformat()}). Renew it with: {RENEW_COMMAND}"
            )
        return None

    def close(self) -> None:
        self._setup.stop()

    def open_setup(self, on_saved: Callable[[StoredCredential], None] | None = None) -> str:
        """Open setup deliberately — startup and `--setup`, never a tool call.

        `on_saved` is for a caller that needs to know a credential was *entered*, which
        is not the same question as whether one exists: `--setup` run to replace a
        working token would otherwise see the old one and declare victory immediately.
        """

        def saved(stored: StoredCredential) -> None:
            self.accept_saved(stored)
            if on_saved is not None:
                on_saved(stored)

        return self._setup.start(saved)

    def _open(self, because: str) -> SetupRequired:
        return SetupRequired(self.open_setup(), because)

"""The credential, supplied by the environment instead of entered into a keychain.

This exists for containers, and it is a knowing departure from ADR-0003, which says the
credential lives in the OS keychain and the server stops rather than storing it anywhere
else. A container has no keychain, and the setup page cannot help: it binds a random
loopback port inside the container, which a browser on the host cannot reach.

So the trade is made explicitly rather than by degrading quietly (ADR-0007):

  * It is **opt-in by construction**. Nothing falls back to the environment. Both
    variables must be set, which is not something that happens by accident.
  * It is **loud**. Startup says the credential came from the environment and what that
    costs, because `docker inspect` and a process listing will show it.
  * It is **read-only**. This store refuses to save, so the setup page can never write a
    credential into a process that has nowhere durable to put one.

On a workstation, use the keychain. This is for the case where there is no keychain to
use, and it should look like the exception it is.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date, timedelta

from loguru import logger

from .credentials import Credential, CredentialError, StoredCredential

EMAIL_ENV = "BB_MCP_EMAIL"
TOKEN_ENV = "BB_MCP_API_TOKEN"
EXPIRY_ENV = "BB_MCP_TOKEN_EXPIRES_ON"

# Without a stated expiry there is nothing to warn about. A year matches Atlassian's own
# default and is a guess, so the warning it produces says where the date came from.
ASSUMED_LIFETIME = timedelta(days=365)


class NotStorable(CredentialError):
    """Raised on any attempt to write a credential the environment supplied."""


@dataclass(frozen=True, slots=True)
class EnvironmentStore:
    """A credential passed in by whoever started the process. Read-only, by design."""

    email: str
    token: str
    expires_on: date

    @classmethod
    def configured(cls, today: date | None = None) -> EnvironmentStore | None:
        """The store, or None when the environment does not carry a credential."""
        email = os.environ.get(EMAIL_ENV, "").strip()
        token = os.environ.get(TOKEN_ENV, "").strip()
        if not email or not token:
            return None

        now = today or date.today()
        return cls(email=email, token=token, expires_on=_expiry(now))

    def load(self) -> StoredCredential:
        """The credential. Malformed input raises here rather than at the first tool call."""
        return StoredCredential(
            credential=Credential(email=self.email, token=self.token),
            expires_on=self.expires_on,
        )

    def save(self, stored: StoredCredential) -> None:
        raise NotStorable(
            "This server was given its credential through the environment, so it has "
            "nowhere to store a new one — an environment variable cannot be written back "
            "to, and writing it to a file is what ADR-0003 refuses. Run setup on a "
            "machine with a keychain, or restart this process with the new token in "
            f"{EMAIL_ENV} and {TOKEN_ENV}."
        )

    def clear(self) -> None:
        raise NotStorable("A credential from the environment is cleared by unsetting it.")

    def announce(self) -> None:
        """Say what was traded away. Once, at startup, where somebody might read it."""
        logger.warning(
            "Using the credential in {} — not the OS keychain (ADR-0007). It is visible "
            "to anything that can read this process's environment, including `docker "
            "inspect`. Setup is unavailable in this mode.",
            TOKEN_ENV,
        )


def _expiry(today: date) -> date:
    stated = os.environ.get(EXPIRY_ENV, "").strip()
    if not stated:
        return today + ASSUMED_LIFETIME

    try:
        return date.fromisoformat(stated)
    except ValueError as exc:
        raise CredentialError(
            f"{EXPIRY_ENV} is {stated!r}, which is not a date. Give the token's expiry as "
            "YYYY-MM-DD, or leave it unset."
        ) from exc


class SetupUnavailable:
    """Stands in for the setup listener where there is nothing for it to write to.

    The gate opens setup on three observed facts. In this mode all three are still
    possible — a missing, expired or rejected credential — and none of them can be fixed
    from inside the container, so the answer has to say where they *can* be fixed.
    """

    def start(self, on_saved) -> str:
        raise CredentialError(
            "This server is running with its credential supplied by the environment, so "
            "it cannot open the setup page: there is no keychain here to save into. Fix "
            f"the credential where the process is started — set {EMAIL_ENV} and "
            f"{TOKEN_ENV} to a working pair and restart. On a workstation, run "
            "`uv run bb-pr-mcp --setup` instead."
        )

    def stop(self) -> None:
        return None

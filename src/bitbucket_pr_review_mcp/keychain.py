"""The credential's only resting place: the operating system keychain.

ADR-0003 chose a long-lived Atlassian API token over OAuth, and named storage as the
price: there is no two-hour expiry to bound the damage, so the secret goes into the
keychain or the server does not run. There is deliberately no file fallback and no
"development mode" — a fallback is how a secret ends up in a dotfile that gets synced,
and the failure would be silent, which is the worst property a security decision can
have.

This module speaks to the `keyring` library and nothing else. It imports no HTTP, no
MCP, and no settings.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date

import keyring
from keyring.errors import KeyringError
from loguru import logger

from .credentials import Credential, CredentialError, StoredCredential
from .environment import EMAIL_ENV, TOKEN_ENV

SERVICE = "bitbucket-pr-review-mcp"
ACCOUNT = "credential"  # one Reviewer per device, so one entry (ADR-0003)

# Loading must not re-run the "expiry is in the future" check that entry does: a
# credential that expired yesterday has to load, so the server can say so and offer setup.
_DISTANT_PAST = date(1970, 1, 1)


class KeychainUnavailable(CredentialError):
    """The OS keychain could not be reached. We refuse rather than store a file."""


@dataclass(frozen=True, slots=True)
class Keychain:
    """Read and write the one credential this device holds."""

    service: str = SERVICE
    account: str = ACCOUNT

    def load(self) -> StoredCredential | None:
        """The stored credential, or None when there is not one. Raises if the keychain is."""
        raw = self._guarded(lambda: keyring.get_password(self.service, self.account), "read")
        if not raw:
            return None
        return self._decode(raw)

    def save(self, stored: StoredCredential) -> None:
        payload = json.dumps(
            {
                "email": stored.credential.email,
                "token": stored.credential.token,
                "expires_on": stored.expires_on.isoformat(),
            }
        )
        self._guarded(
            lambda: keyring.set_password(self.service, self.account, payload), "write to"
        )
        logger.info("Credential for {} stored in the OS keychain.", stored.credential.email)

    def clear(self) -> None:
        """Forget the credential. Removing one that isn't there is not an error."""
        try:
            keyring.delete_password(self.service, self.account)
        except keyring.errors.PasswordDeleteError:
            return
        except KeyringError as exc:
            raise _unavailable("clear", exc) from exc

    def _decode(self, raw: str) -> StoredCredential | None:
        """A blob we cannot read is treated as no credential, not as a crash at startup."""
        try:
            body = json.loads(raw)
            return StoredCredential.of(
                email=body["email"],
                token=body["token"],
                expires_on=body["expires_on"],
                today=_DISTANT_PAST,
            )
        except (ValueError, KeyError, TypeError, CredentialError) as exc:
            logger.warning(
                "The keychain entry for {} is unreadable ({}); treating it as absent so "
                "setup can replace it.",
                self.service,
                exc,
            )
            return None

    def _guarded(self, call, verb: str):
        try:
            return call()
        except KeyringError as exc:
            raise _unavailable(verb, exc) from exc


def _unavailable(verb: str, exc: Exception) -> KeychainUnavailable:
    return KeychainUnavailable(
        f"Cannot {verb} the OS keychain ({exc}). This server stores the Atlassian API "
        "token in the keychain and nowhere else, so it stops here rather than falling "
        "back to a file (ADR-0003).\n"
        "  * On Linux, this usually means no Secret Service is running: install "
        "gnome-keyring or KWallet, unlock it, and try again.\n"
        "  * In a container there is no keychain at all. Supply the credential through "
        f"the environment instead — {EMAIL_ENV} and {TOKEN_ENV} — which is a deliberate "
        "trade documented in ADR-0007, not a fallback this server takes on its own."
    )

__all__ = ["ACCOUNT", "SERVICE", "Credential", "Keychain", "KeychainUnavailable"]

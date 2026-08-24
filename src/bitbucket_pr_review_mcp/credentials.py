"""The Reviewer's Bitbucket credential.

An Atlassian API token, used with Basic authentication where the username is the
**Atlassian account email** — not a Bitbucket username, and not the token's name.
Bitbucket returns 401 with an empty body for the other two, which is among the least
diagnosable failures on this API, so the distinction is enforced here rather than
discovered later (ADR-0003).

The credential carries the expiry date the Reviewer entered. Bitbucket does not tell
us when an API token dies, so the alternative to asking is an unexplained 401 in the
middle of a review; a date the Reviewer typed is worth more than no date at all, even
though nothing verifies it.

This module is plain data. It knows nothing of keychains (see `keychain.py`), nothing of
HTTP, and nothing of MCP.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass
from datetime import date


class CredentialError(RuntimeError):
    """Raised when no usable credential is available. The message says how to fix it."""


@dataclass(frozen=True, slots=True)
class Credential:
    """An Atlassian account email and API token."""

    email: str
    token: str

    def __post_init__(self) -> None:
        if "@" not in self.email:
            raise CredentialError(
                f"{self.email!r} is not an Atlassian account email. Basic authentication to "
                "Bitbucket Cloud uses your Atlassian email address; a Bitbucket username or "
                "the token's name returns 401 with nothing useful in the body."
            )
        if not self.token.strip():
            raise CredentialError("The API token is empty.")

    def authorization_header(self) -> str:
        encoded = base64.b64encode(f"{self.email}:{self.token}".encode()).decode("ascii")
        return f"Basic {encoded}"

    def __repr__(self) -> str:
        # A credential ends up in tracebacks and log lines. The token does not.
        return f"Credential(email={self.email!r}, token=<redacted>)"


@dataclass(frozen=True, slots=True)
class StoredCredential:
    """A credential together with the expiry date the Reviewer gave it."""

    credential: Credential
    expires_on: date

    @classmethod
    def of(
        cls,
        *,
        email: str,
        token: str,
        expires_on: str | date,
        today: date | None = None,
    ) -> StoredCredential:
        """Build one from form input, refusing anything that would be stored unusable."""
        parsed = expires_on if isinstance(expires_on, date) else _parse_date(expires_on)
        if parsed < (today or date.today()):
            raise ValueError(
                f"{parsed.isoformat()} is in the past. Enter the date the token itself "
                "expires — Atlassian shows it on the token list."
            )
        credential = Credential(email=email.strip(), token=token.strip())
        return cls(credential=credential, expires_on=parsed)

    @property
    def email(self) -> str:
        return self.credential.email

    def is_expired(self, today: date | None = None) -> bool:
        return self.expires_on < (today or date.today())

    def usable_on(self, today: date | None = None) -> bool:
        return not self.is_expired(today)

    def days_left(self, today: date | None = None) -> int:
        return (self.expires_on - (today or date.today())).days

    def __repr__(self) -> str:
        return f"StoredCredential({self.credential!r}, expires_on={self.expires_on.isoformat()})"


def _parse_date(raw: str) -> date:
    try:
        return date.fromisoformat(raw.strip())
    except ValueError as exc:
        raise ValueError(
            f"{raw!r} is not a date. Give the token's expiry as YYYY-MM-DD."
        ) from exc

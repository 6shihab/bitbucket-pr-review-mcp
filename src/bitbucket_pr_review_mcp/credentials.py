"""The Reviewer's Bitbucket credential.

An Atlassian API token, used with Basic authentication where the username is the
**Atlassian account email** — not a Bitbucket username, and not the token's name.
Bitbucket returns 401 with an empty body for the other two, which is among the least
diagnosable failures on this API, so the distinction is enforced here rather than
discovered later (ADR-0003).

Ticket 01 reads the credential from the environment. Ticket 02 replaces that source
with the OS keychain and the browser setup page; this module's shape does not change.
"""

from __future__ import annotations

import base64
import os
from dataclasses import dataclass

EMAIL_ENV = "BB_MCP_EMAIL"
TOKEN_ENV = "BB_MCP_API_TOKEN"


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


def from_environment() -> Credential:
    email = os.environ.get(EMAIL_ENV, "").strip()
    token = os.environ.get(TOKEN_ENV, "").strip()
    if not email or not token:
        raise CredentialError(
            f"No credential. Set {EMAIL_ENV} to your Atlassian account email and "
            f"{TOKEN_ENV} to an API token granting repository read and pull request write."
        )
    return Credential(email=email, token=token)

"""A real keyring backend that keeps secrets in a dict.

Ticket 02 asks for storage to be tested "against the keyring library's in-memory
backend, not a bespoke abstraction". keyring 25 ships no in-memory backend — only
`fail`, `null`, and the platform ones — so this is the nearest honest reading: a real
`KeyringBackend` installed through `keyring.set_keyring`, so the production code goes
through the real keyring API and the seam is the backend rather than our own wrapper.
"""

from __future__ import annotations

from keyring.backend import KeyringBackend
from keyring.errors import PasswordDeleteError


class InMemoryKeyring(KeyringBackend):
    """Stores passwords in a dict for the lifetime of one test."""

    priority = 1  # type: ignore[assignment]

    def __init__(self) -> None:
        super().__init__()
        self.secrets: dict[tuple[str, str], str] = {}

    def get_password(self, service: str, username: str) -> str | None:
        return self.secrets.get((service, username))

    def set_password(self, service: str, username: str, password: str) -> None:
        self.secrets[(service, username)] = password

    def delete_password(self, service: str, username: str) -> None:
        if (service, username) not in self.secrets:
            raise PasswordDeleteError(f"no password for {service}/{username}")
        del self.secrets[(service, username)]

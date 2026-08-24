"""Where a shared server keeps other people's Bitbucket credentials.

The per-device server puts one credential in the OS keychain and refuses to store a file
(ADR-0003). A shared server cannot: a keychain holds one credential for the person
running the process, and there is no arrangement of it that holds a colleague's token on
a server that colleague has never logged into. So this exists, and ADR-0008 records the
downgrade rather than pretending it is an equivalent.

What is actually bought here is narrower than "encrypted at rest" sounds, and
`docs/threat-model-shared-store.md` says so adversary by adversary. Two properties:

* Somebody who gets the file and not the key learns how many people are enrolled and
  their opaque ids, and nothing else.
* The person's id is authenticated as associated data, so a row cannot be read as
  somebody else's. A bug in a lookup fails to decrypt instead of quietly returning a
  colleague's token — which, on a server that posts comments under people's names, is the
  failure worth spending cryptography on.

Everything else — an operator with shell, a compromise of this process — gets every
token at once, and no cipher changes that. What bounds it is branch restrictions on the
repositories and short token expiries, both of which live outside this file.

This module speaks to SQLite and to one AEAD. It imports no HTTP, no MCP and no settings.
"""

from __future__ import annotations

import base64
import binascii
import json
import os
import re
import secrets
import sqlite3
import threading
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from loguru import logger

from .credentials import Credential, CredentialError, StoredCredential

KEY_ENV = "BB_MCP_VAULT_KEY"
KEY_FILE_ENV = "BB_MCP_VAULT_KEY_FILE"

KEY_BYTES = 32
NONCE_BYTES = 12

# Bumped only if the sealed payload changes shape. It is inside the associated data, so
# a payload from one format cannot be read as another.
SEAL_FORMAT = 1

_BASE64 = re.compile(r"[A-Za-z0-9_\-+/]+={0,2}")

_OWNER_ONLY = 0o600


class VaultError(CredentialError):
    """Something is wrong with the credential store. The message says what to do."""


class VaultKeyMissing(VaultError):
    """No encryption key was supplied. The server stops rather than inventing one."""


class VaultUnreadable(VaultError):
    """A stored credential will not decrypt: wrong key, or the row has been altered."""


@dataclass(frozen=True, slots=True)
class VaultKey:
    """The key every stored credential is sealed under. Supplied at startup, never
    written beside the data, and never printed."""

    material: bytes

    def __post_init__(self) -> None:
        if len(self.material) != KEY_BYTES:
            raise VaultError(f"A vault key is {KEY_BYTES} bytes; this one is {len(self.material)}.")

    @classmethod
    def generate(cls) -> VaultKey:
        """A new key, for an operator to store somewhere the database backups do not
        reach. Nothing in the server calls this: see `required`."""
        return cls(secrets.token_bytes(KEY_BYTES))

    @classmethod
    def parse(cls, raw: str) -> VaultKey:
        text = raw.strip()
        if not text or not _BASE64.fullmatch(text):
            raise VaultError(
                f"That is not a vault key. Expected {KEY_BYTES} bytes, base64-encoded."
            )
        try:
            material = base64.urlsafe_b64decode(_padded(text))
        except (binascii.Error, ValueError) as exc:
            raise VaultError(
                f"That is not a vault key. Expected {KEY_BYTES} bytes, base64-encoded."
            ) from exc
        return cls(material)

    @classmethod
    def required(cls, environ: Mapping[str, str] | None = None) -> VaultKey:
        """The key this deployment was given, or a refusal to start.

        Nothing generates one on the fly. A generated key is a key nobody backed up, and
        a key nobody backed up is every stored credential lost on the next restart —
        silently, which is the worst property a failure can have.
        """
        source = environ if environ is not None else os.environ

        supplied = (source.get(KEY_ENV) or "").strip()
        if supplied:
            return cls.parse(supplied)

        named = (source.get(KEY_FILE_ENV) or "").strip()
        if named:
            try:
                return cls.parse(Path(named).read_text(encoding="utf-8"))
            except OSError as exc:
                raise VaultKeyMissing(
                    f"{KEY_FILE_ENV} names {named}, which cannot be read ({exc.strerror}). "
                    "The shared server will not start without its key."
                ) from exc

        raise VaultKeyMissing(
            "This server holds other people's Bitbucket credentials and has no key to "
            f"seal them with. Set {KEY_ENV} to a base64 {KEY_BYTES}-byte key, or "
            f"{KEY_FILE_ENV} to a file holding one.\n"
            "It does not generate one for you on purpose: a generated key is a key "
            "nobody backed up, and a key nobody backed up is every stored credential "
            "lost on the next restart. Make one, back it up somewhere the database "
            "backups do not reach, then start the server."
        )

    def exported(self) -> str:
        """The form an operator stores. Called by key generation and by nothing else."""
        return base64.urlsafe_b64encode(self.material).decode("ascii")

    def __repr__(self) -> str:
        return "VaultKey(<redacted>)"


@dataclass(frozen=True, slots=True)
class Enrolment:
    """What an operator may see about somebody: everything except their token.

    `email` and `expires_on` are None for a row that will not decrypt. One damaged row
    should not blind an operator to everybody else.
    """

    person: str
    connected_at: datetime
    email: str | None = None
    expires_on: date | None = None

    @property
    def readable(self) -> bool:
        return self.email is not None


class CredentialVault:
    """One SQLite file, one row per enrolled person, each row sealed under the key."""

    def __init__(self, connection: sqlite3.Connection, key: VaultKey) -> None:
        self._connection = connection
        self._key = key
        self._lock = threading.Lock()

    @classmethod
    def at(cls, path: Path | str, key: VaultKey) -> CredentialVault:
        """Open the store, creating it owner-only if it is not there yet."""
        location = Path(path)
        location.parent.mkdir(parents=True, exist_ok=True)
        _create_owner_only(location)

        connection = sqlite3.connect(location, check_same_thread=False)
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute(
            "CREATE TABLE IF NOT EXISTS credentials ("
            "  person       TEXT PRIMARY KEY,"
            "  sealed       BLOB NOT NULL,"
            "  seal_format  INTEGER NOT NULL,"
            "  connected_at TEXT NOT NULL"
            ")"
        )
        connection.commit()
        return cls(connection, key)

    def for_person(self, person: str) -> PersonalCredentials:
        """This person's view of the store, shaped like the keychain so that
        `CredentialGate` works over either without knowing which it has."""
        return PersonalCredentials(self, _named(person))

    def save(self, person: str, stored: StoredCredential) -> None:
        who = _named(person)
        sealed = self._seal(who, stored)
        with self._lock:
            self._connection.execute(
                "INSERT INTO credentials (person, sealed, seal_format, connected_at) "
                "VALUES (?, ?, ?, ?) "
                "ON CONFLICT(person) DO UPDATE SET "
                "  sealed = excluded.sealed,"
                "  seal_format = excluded.seal_format,"
                "  connected_at = excluded.connected_at",
                (who, sealed, SEAL_FORMAT, datetime.now(UTC).isoformat()),
            )
            self._connection.commit()
        logger.info("Credential for {} stored for {}.", stored.credential.email, who)

    def load(self, person: str) -> StoredCredential | None:
        who = _named(person)
        with self._lock:
            row = self._connection.execute(
                "SELECT sealed, seal_format FROM credentials WHERE person = ?", (who,)
            ).fetchone()
        if row is None:
            return None
        return self._open(who, row[0], row[1], self._key)

    def clear(self, person: str) -> None:
        """Forget this person's credential. Forgetting one that isn't there is fine."""
        who = _named(person)
        with self._lock:
            self._connection.execute("DELETE FROM credentials WHERE person = ?", (who,))
            self._connection.commit()
        logger.info("The stored credential for {} has been removed.", who)

    def enrolled(self) -> list[Enrolment]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT person, sealed, seal_format, connected_at FROM credentials "
                "ORDER BY connected_at"
            ).fetchall()

        enrolments = []
        for who, sealed, seal_format, connected in rows:
            when = datetime.fromisoformat(connected)
            try:
                stored = self._open(who, sealed, seal_format, self._key)
            except VaultUnreadable:
                logger.warning("The stored credential for {} will not decrypt.", who)
                enrolments.append(Enrolment(person=who, connected_at=when))
                continue
            enrolments.append(
                Enrolment(
                    person=who,
                    connected_at=when,
                    email=stored.credential.email,
                    expires_on=stored.expires_on,
                )
            )
        return enrolments

    def rotate(self, replacement: VaultKey) -> int:
        """Re-seal every credential under a new key. Nobody re-enrols.

        A suspected key exposure should be an operation an operator can perform on a
        Tuesday, not an onboarding exercise for the whole team.
        """
        with self._lock:
            rows = self._connection.execute(
                "SELECT person, sealed, seal_format FROM credentials"
            ).fetchall()

            resealed = [
                (self._seal(who, self._open(who, sealed, seal_format, self._key), replacement), who)
                for who, sealed, seal_format in rows
            ]
            self._connection.executemany(
                "UPDATE credentials SET sealed = ?, seal_format = ? WHERE person = ?",
                [(sealed, SEAL_FORMAT, who) for sealed, who in resealed],
            )
            self._connection.commit()
            self._key = replacement

        logger.info("Re-sealed {} stored credential(s) under a new vault key.", len(resealed))
        return len(resealed)

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    def _seal(self, person: str, stored: StoredCredential, key: VaultKey | None = None) -> bytes:
        payload = json.dumps(
            {
                "email": stored.credential.email,
                "token": stored.credential.token,
                "expires_on": stored.expires_on.isoformat(),
            }
        ).encode("utf-8")
        nonce = secrets.token_bytes(NONCE_BYTES)
        cipher = AESGCM((key or self._key).material)
        return nonce + cipher.encrypt(nonce, payload, _bound_to(person, SEAL_FORMAT))

    def _open(
        self, person: str, sealed: bytes, seal_format: int, key: VaultKey
    ) -> StoredCredential:
        try:
            cipher = AESGCM(key.material)
            payload = cipher.decrypt(
                sealed[:NONCE_BYTES], sealed[NONCE_BYTES:], _bound_to(person, seal_format)
            )
            body = json.loads(payload)
            return StoredCredential(
                credential=Credential(email=body["email"], token=body["token"]),
                expires_on=date.fromisoformat(body["expires_on"]),
            )
        except (InvalidTag, ValueError, KeyError, TypeError, CredentialError) as exc:
            raise VaultUnreadable(
                f"The stored credential for {person} will not decrypt. Either this server "
                f"was given the wrong key ({KEY_ENV}), or the row has been altered — a "
                "credential is sealed against the person it belongs to, so a row moved "
                "between people fails here rather than being handed to the wrong one."
            ) from exc


@dataclass(frozen=True, slots=True)
class PersonalCredentials:
    """One person's slice of the vault, satisfying the `CredentialStore` protocol.

    This is the whole reason the gate needs no change on a shared server: it asks a store
    for "the credential", and which person that means was decided when this was made —
    from the authenticated session, never from a tool argument.
    """

    vault: CredentialVault
    person: str

    def load(self) -> StoredCredential | None:
        return self.vault.load(self.person)

    def save(self, stored: StoredCredential) -> None:
        self.vault.save(self.person, stored)

    def clear(self) -> None:
        self.vault.clear(self.person)


def _bound_to(person: str, seal_format: int) -> bytes:
    """The associated data: what this ciphertext is allowed to be. Changing either half
    makes the row unreadable rather than readable as something else."""
    return f"bitbucket-pr-review-mcp/vault/{seal_format}/{person}".encode()


def _named(person: str) -> str:
    who = person.strip()
    if not who:
        raise VaultError("A credential is stored against a person, and none was named.")
    return who


def _padded(text: str) -> str:
    return text + "=" * (-len(text) % 4)


def _create_owner_only(location: Path) -> None:
    """Create the file before SQLite does, so it is never briefly world-readable."""
    if location.exists():
        return
    try:
        os.close(os.open(location, os.O_CREAT | os.O_EXCL | os.O_WRONLY, _OWNER_ONLY))
    except FileExistsError:
        return


__all__ = [
    "KEY_ENV",
    "KEY_FILE_ENV",
    "CredentialVault",
    "Enrolment",
    "PersonalCredentials",
    "VaultError",
    "VaultKey",
    "VaultKeyMissing",
    "VaultUnreadable",
]

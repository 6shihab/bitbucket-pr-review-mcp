"""The store that holds other people's tokens.

Every test here uses a real SQLite file in a temporary directory. A fake store would
prove the tests pass; the questions worth asking are about what is on the disk, and a
fake has no disk. See `docs/threat-model-shared-store.md` — the numbered requirements at
its end are what these tests check.
"""

from __future__ import annotations

import os
import sqlite3
from datetime import date, datetime
from pathlib import Path

import pytest
from loguru import logger

from bitbucket_pr_review_mcp.credentials import Credential, CredentialError, StoredCredential
from bitbucket_pr_review_mcp.gate import CredentialGate
from bitbucket_pr_review_mcp.vault import (
    KEY_ENV,
    KEY_FILE_ENV,
    CredentialVault,
    VaultError,
    VaultKey,
    VaultKeyMissing,
    VaultUnreadable,
)

TOKEN = "ATATT-if-you-can-read-this-the-vault-leaked-3245308C"
OTHER_TOKEN = "ATATT-a-completely-different-secret-CB232512"

ALICE = "person-01HQ8Z"
BOB = "person-01HQ9A"


def credential_for(email: str, token: str = TOKEN) -> StoredCredential:
    return StoredCredential(
        credential=Credential(email=email, token=token), expires_on=date(2099, 1, 1)
    )


@pytest.fixture
def key() -> VaultKey:
    return VaultKey.generate()


@pytest.fixture
def vault_path(tmp_path):
    return tmp_path / "credentials.sqlite3"


@pytest.fixture
def vault(vault_path, key):
    store = CredentialVault.at(vault_path, key)
    yield store
    store.close()


class TestTheKey:
    def test_a_generated_key_round_trips_through_its_exported_form(self):
        generated = VaultKey.generate()

        assert VaultKey.parse(generated.exported()) == generated

    def test_two_generated_keys_differ(self):
        assert VaultKey.generate() != VaultKey.generate()

    @pytest.mark.parametrize("raw", ["", "   ", "not base64 at all", "c2hvcnQ=", "AAAAA"])
    def test_a_key_that_is_not_one_is_refused_at_the_door(self, raw):
        with pytest.raises(CredentialError):
            VaultKey.parse(raw)

    def test_an_absent_key_stops_the_server_rather_than_being_invented(self, monkeypatch):
        monkeypatch.delenv(KEY_ENV, raising=False)
        monkeypatch.delenv(KEY_FILE_ENV, raising=False)

        with pytest.raises(VaultKeyMissing) as caught:
            VaultKey.required()

        assert KEY_ENV in str(caught.value)

    def test_the_refusal_says_why_it_will_not_invent_one(self, monkeypatch):
        monkeypatch.delenv(KEY_ENV, raising=False)
        monkeypatch.delenv(KEY_FILE_ENV, raising=False)

        with pytest.raises(VaultKeyMissing) as caught:
            VaultKey.required()

        assert "back" in str(caught.value).lower(), (
            "a key nobody backed up is every credential lost on the next restart"
        )

    def test_it_can_come_from_the_environment(self, monkeypatch, key):
        monkeypatch.setenv(KEY_ENV, key.exported())

        assert VaultKey.required() == key

    def test_it_can_come_from_a_mounted_file(self, monkeypatch, key, tmp_path):
        secret = tmp_path / "vault.key"
        secret.write_text(key.exported() + "\n", encoding="utf-8")
        monkeypatch.delenv(KEY_ENV, raising=False)
        monkeypatch.setenv(KEY_FILE_ENV, str(secret))

        assert VaultKey.required() == key

    def test_a_named_file_that_is_not_there_is_an_error_not_a_shrug(self, monkeypatch, tmp_path):
        monkeypatch.delenv(KEY_ENV, raising=False)
        monkeypatch.setenv(KEY_FILE_ENV, str(tmp_path / "absent.key"))

        with pytest.raises(VaultKeyMissing):
            VaultKey.required()

    def test_a_directory_where_the_key_should_be_says_where_it_came_from(
        self, monkeypatch, tmp_path
    ):
        """The commonest way this deployment breaks, and the least legible.

        Docker creates a bind mount's source path when it does not exist, and it creates
        a *directory*. So a key file that was never made arrives inside the container as
        a directory named `vault.key` — on Linux the mount itself fails first, with an
        OCI error about mounting a directory onto a file, and nothing anywhere says
        "there is no key". Saying it here is the only place it fits.
        """
        as_directory = tmp_path / "vault.key"
        as_directory.mkdir()
        monkeypatch.delenv(KEY_ENV, raising=False)
        monkeypatch.setenv(KEY_FILE_ENV, str(as_directory))

        with pytest.raises(VaultKeyMissing) as caught:
            VaultKey.required()

        message = str(caught.value)
        assert "directory" in message.lower()
        assert "did not exist" in message, "the cause, not just the symptom"

    def test_an_unreadable_key_says_which_user_could_not_read_it(
        self, monkeypatch, tmp_path
    ):
        """The second half of the same deployment story.

        A key made with `sudo` is owned by root and mode 600, and this server runs as an
        unprivileged user inside the container. "Permission denied" is then true and
        useless: the file is plainly there, and the reader is not named anywhere.
        """
        secret = tmp_path / "vault.key"
        secret.write_text("irrelevant", encoding="utf-8")

        def refuse(*args, **kwargs):
            raise PermissionError(13, "Permission denied")

        monkeypatch.setattr(Path, "read_text", refuse)
        monkeypatch.delenv(KEY_ENV, raising=False)
        monkeypatch.setenv(KEY_FILE_ENV, str(secret))

        with pytest.raises(VaultKeyMissing) as caught:
            VaultKey.required()

        message = str(caught.value)
        assert "Permission denied" in message
        assert "readable by" in message, "say who has to be able to read it"

    def test_an_unwritable_store_directory_says_who_could_not_write_it(
        self, monkeypatch, tmp_path, key
    ):
        """The third way this deployment refuses to start, and the last one with a
        traceback instead of a sentence.

        The image chowns `/vault` to the user this runs as, which is enough for a named
        volume — Docker seeds one from the image. A bind mount keeps the host directory's
        ownership instead, so the same compose file works one way and not the other, and
        the difference surfaces as an unhandled PermissionError from `os.open`.
        """

        def refuse(*args, **kwargs):
            raise PermissionError(13, "Permission denied")

        monkeypatch.setattr(os, "open", refuse)

        with pytest.raises(VaultError) as caught:
            CredentialVault.at(tmp_path / "credentials.sqlite3", key)

        message = str(caught.value)
        assert "writable by" in message, "say who has to be able to write there"
        assert "bind" in message.lower(), "and why it usually is not"

    def test_the_key_does_not_print_itself(self, key):
        assert key.exported() not in repr(key)
        assert key.exported() not in str(key)


class TestStoringAndReadingBack:
    def test_what_goes_in_comes_out(self, vault):
        vault.save(ALICE, credential_for("alice@streamstech.com"))

        stored = vault.load(ALICE)

        assert stored.credential.email == "alice@streamstech.com"
        assert stored.credential.token == TOKEN
        assert stored.expires_on == date(2099, 1, 1)

    def test_somebody_who_has_not_connected_has_nothing(self, vault):
        assert vault.load(ALICE) is None

    def test_connecting_again_replaces_the_old_token(self, vault):
        vault.save(ALICE, credential_for("alice@streamstech.com"))
        vault.save(ALICE, credential_for("alice@streamstech.com", OTHER_TOKEN))

        assert vault.load(ALICE).credential.token == OTHER_TOKEN
        assert len(vault.enrolled()) == 1

    def test_a_credential_can_be_deleted(self, vault):
        vault.save(ALICE, credential_for("alice@streamstech.com"))

        vault.clear(ALICE)

        assert vault.load(ALICE) is None

    def test_deleting_nothing_is_not_an_error(self, vault):
        vault.clear(ALICE)

    def test_an_expired_credential_still_loads_so_the_server_can_say_so(self, vault):
        vault.save(
            ALICE,
            StoredCredential(
                credential=Credential(email="alice@streamstech.com", token=TOKEN),
                expires_on=date(2001, 1, 1),
            ),
        )

        assert vault.load(ALICE).is_expired(date(2026, 1, 1))

    def test_it_survives_the_process_that_wrote_it(self, vault_path, key):
        first = CredentialVault.at(vault_path, key)
        first.save(ALICE, credential_for("alice@streamstech.com"))
        first.close()

        second = CredentialVault.at(vault_path, key)
        assert second.load(ALICE).credential.token == TOKEN
        second.close()

    def test_a_person_must_be_named(self, vault):
        with pytest.raises(CredentialError):
            vault.save("   ", credential_for("alice@streamstech.com"))


class TestSomebodyWithTheFileAndNothingElse:
    """Adversary 1 in the threat model, and the only one the encryption defeats."""

    @pytest.fixture
    def raw(self, vault, vault_path) -> bytes:
        vault.save(ALICE, credential_for("alice@streamstech.com"))
        vault.close()
        return vault_path.read_bytes()

    def test_the_token_is_not_in_the_file(self, raw):
        assert TOKEN.encode() not in raw

    def test_the_email_is_not_in_the_file(self, raw):
        assert b"alice@streamstech.com" not in raw

    def test_the_key_is_not_in_the_file(self, raw, key):
        assert key.exported().encode() not in raw

    def test_the_enrolment_id_is(self, raw):
        """Stated in the threat model rather than pretended away: the file says how many
        people are enrolled and what their opaque ids are."""
        assert ALICE.encode() in raw

    def test_a_wrong_key_cannot_read_it(self, vault, vault_path):
        vault.save(ALICE, credential_for("alice@streamstech.com"))
        vault.close()

        impostor = CredentialVault.at(vault_path, VaultKey.generate())

        with pytest.raises(VaultUnreadable):
            impostor.load(ALICE)
        impostor.close()


class TestPeopleAreNotInterchangeable:
    def test_two_people_get_their_own_credentials(self, vault):
        vault.save(ALICE, credential_for("alice@streamstech.com"))
        vault.save(BOB, credential_for("bob@streamstech.com", OTHER_TOKEN))

        assert vault.load(ALICE).credential.token == TOKEN
        assert vault.load(BOB).credential.token == OTHER_TOKEN

    def test_a_row_moved_between_people_fails_loudly(self, vault, vault_path, key):
        """The property worth buying with cryptography: the person's id is authenticated,
        so a bug in a lookup cannot quietly hand back somebody else's token."""
        vault.save(ALICE, credential_for("alice@streamstech.com"))
        vault.save(BOB, credential_for("bob@streamstech.com", OTHER_TOKEN))
        vault.close()

        moved = sqlite3.connect(vault_path)
        sealed = moved.execute(
            "SELECT sealed FROM credentials WHERE person = ?", (ALICE,)
        ).fetchone()[0]
        moved.execute("UPDATE credentials SET sealed = ? WHERE person = ?", (sealed, BOB))
        moved.commit()
        moved.close()

        tampered = CredentialVault.at(vault_path, key)
        with pytest.raises(VaultUnreadable):
            tampered.load(BOB)
        tampered.close()

    def test_deleting_one_person_leaves_the_other_alone(self, vault):
        vault.save(ALICE, credential_for("alice@streamstech.com"))
        vault.save(BOB, credential_for("bob@streamstech.com", OTHER_TOKEN))

        vault.clear(ALICE)

        assert vault.load(ALICE) is None
        assert vault.load(BOB) is not None


class TestRotation:
    def test_every_row_moves_to_the_new_key(self, vault, vault_path):
        vault.save(ALICE, credential_for("alice@streamstech.com"))
        vault.save(BOB, credential_for("bob@streamstech.com", OTHER_TOKEN))
        replacement = VaultKey.generate()

        assert vault.rotate(replacement) == 2
        vault.close()

        rotated = CredentialVault.at(vault_path, replacement)
        assert rotated.load(ALICE).credential.token == TOKEN
        assert rotated.load(BOB).credential.token == OTHER_TOKEN
        rotated.close()

    def test_the_old_key_stops_working(self, vault, vault_path, key):
        vault.save(ALICE, credential_for("alice@streamstech.com"))
        vault.rotate(VaultKey.generate())
        vault.close()

        stale = CredentialVault.at(vault_path, key)
        with pytest.raises(VaultUnreadable):
            stale.load(ALICE)
        stale.close()

    def test_nobody_has_to_re_enrol(self, vault):
        vault.save(ALICE, credential_for("alice@streamstech.com"))
        before = [enrolment.person for enrolment in vault.enrolled()]

        vault.rotate(VaultKey.generate())

        assert [enrolment.person for enrolment in vault.enrolled()] == before

    def test_the_vault_keeps_working_after_rotating_itself(self, vault):
        vault.save(ALICE, credential_for("alice@streamstech.com"))

        vault.rotate(VaultKey.generate())

        assert vault.load(ALICE).credential.token == TOKEN

    def test_rotating_an_empty_vault_is_not_an_error(self, vault):
        assert vault.rotate(VaultKey.generate()) == 0


class TestWhatAnOperatorCanSee:
    def test_who_is_enrolled_when_they_connected_and_when_their_token_dies(self, vault):
        vault.save(ALICE, credential_for("alice@streamstech.com"))

        enrolment = vault.enrolled()[0]

        assert enrolment.person == ALICE
        assert enrolment.email == "alice@streamstech.com"
        assert enrolment.expires_on == date(2099, 1, 1)
        assert isinstance(enrolment.connected_at, datetime)
        assert enrolment.connected_at.tzinfo is not None

    def test_no_token_is_anywhere_in_that_answer(self, vault):
        vault.save(ALICE, credential_for("alice@streamstech.com"))

        assert TOKEN not in repr(vault.enrolled())

    def test_an_unreadable_row_is_reported_rather_than_crashing_the_listing(
        self, vault, vault_path, key
    ):
        """One damaged row must not make the operator's view of everybody else fail."""
        vault.save(ALICE, credential_for("alice@streamstech.com"))
        vault.save(BOB, credential_for("bob@streamstech.com", OTHER_TOKEN))
        vault.close()

        damaged = sqlite3.connect(vault_path)
        damaged.execute("UPDATE credentials SET sealed = ? WHERE person = ?", (b"rubbish", ALICE))
        damaged.commit()
        damaged.close()

        reopened = CredentialVault.at(vault_path, key)
        listed = {enrolment.person: enrolment for enrolment in reopened.enrolled()}
        reopened.close()

        assert not listed[ALICE].readable, "unreadable, and saying so"
        assert listed[ALICE].email is None
        assert listed[BOB].readable
        assert listed[BOB].email == "bob@streamstech.com"


class TestItDropsIntoTheGateUnchanged:
    def test_a_persons_view_is_a_credential_store(self, vault, setup_listener):
        vault.save(ALICE, credential_for("alice@streamstech.com"))

        gate = CredentialGate(vault.for_person(ALICE), setup_listener)

        assert gate.current().email == "alice@streamstech.com"

    def test_somebody_who_has_not_connected_is_sent_to_setup(self, vault, setup_listener):
        gate = CredentialGate(vault.for_person(BOB), setup_listener)

        with pytest.raises(CredentialError) as caught:
            gate.current()

        assert setup_listener.URL in str(caught.value)

    def test_forgetting_through_the_gate_removes_only_that_person(self, vault, setup_listener):
        vault.save(ALICE, credential_for("alice@streamstech.com"))
        vault.save(BOB, credential_for("bob@streamstech.com", OTHER_TOKEN))

        CredentialGate(vault.for_person(ALICE), setup_listener).forget()

        assert vault.load(ALICE) is None
        assert vault.load(BOB) is not None

    def test_setup_completing_saves_against_that_person_only(self, vault, setup_listener):
        gate = CredentialGate(vault.for_person(ALICE), setup_listener)

        vault.for_person(ALICE).save(credential_for("alice@streamstech.com"))

        assert gate.current().email == "alice@streamstech.com"
        assert vault.load(BOB) is None

    def test_the_credential_it_hands_out_does_not_print_its_token(self, vault):
        vault.save(ALICE, credential_for("alice@streamstech.com"))

        assert TOKEN not in repr(vault.load(ALICE))


class TestNothingItSaysCarriesASecret:
    def test_not_in_any_log_line(self, vault, key):
        said: list[str] = []
        sink = logger.add(said.append, level="DEBUG")
        try:
            vault.save(ALICE, credential_for("alice@streamstech.com"))
            vault.load(ALICE)
            vault.rotate(VaultKey.generate())
            vault.clear(ALICE)
        finally:
            logger.remove(sink)

        spoken = "\n".join(said)
        assert TOKEN not in spoken
        assert key.exported() not in spoken

    def test_not_in_the_error_a_wrong_key_produces(self, vault, vault_path, key):
        vault.save(ALICE, credential_for("alice@streamstech.com"))
        vault.close()
        impostor = CredentialVault.at(vault_path, VaultKey.generate())

        with pytest.raises(VaultUnreadable) as caught:
            impostor.load(ALICE)
        impostor.close()

        assert TOKEN not in str(caught.value)
        assert key.exported() not in str(caught.value)


@pytest.mark.skipif(os.name == "nt", reason="POSIX file modes")
class TestOnDisk:
    def test_the_file_is_readable_only_by_its_owner(self, vault, vault_path):
        vault.save(ALICE, credential_for("alice@streamstech.com"))

        assert vault_path.stat().st_mode & 0o077 == 0

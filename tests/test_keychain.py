"""Where the credential lives: the OS keychain, and nowhere else.

ADR-0003 makes storage the load-bearing part of choosing a long-lived token over OAuth:
there is no 2-hour expiry to limit the damage, so the secret goes into the keychain or
the server refuses to run. The interesting tests here are the refusals — a keychain that
quietly became a file is the failure this module exists to make impossible.
"""

from __future__ import annotations

import json
from datetime import date

import keyring
import pytest
from keyring.backends.fail import Keyring as FailingKeyring

from bitbucket_pr_review_mcp.credentials import Credential, StoredCredential
from bitbucket_pr_review_mcp.keychain import ACCOUNT, SERVICE, Keychain, KeychainUnavailable

TODAY = date(2026, 8, 24)


def stored(expires_on: date = date(2027, 1, 1)) -> StoredCredential:
    return StoredCredential(
        credential=Credential(email="reviewer@streamstech.com", token="a-real-token"),
        expires_on=expires_on,
    )


class TestRoundTrip:
    def test_a_saved_credential_comes_back(self, keychain):
        keychain.save(stored())

        loaded = keychain.load()

        assert loaded is not None
        assert loaded.credential.email == "reviewer@streamstech.com"
        assert loaded.credential.token == "a-real-token"
        assert loaded.expires_on == date(2027, 1, 1)

    def test_nothing_stored_reads_as_nothing(self, keychain):
        assert keychain.load() is None

    def test_it_goes_through_the_real_keyring_api(self, keychain, memory_keyring):
        keychain.save(stored())

        raw = memory_keyring.secrets[(SERVICE, ACCOUNT)]
        assert json.loads(raw)["email"] == "reviewer@streamstech.com"

    def test_clearing_removes_it(self, keychain):
        keychain.save(stored())

        keychain.clear()

        assert keychain.load() is None

    def test_clearing_nothing_is_not_an_error(self, keychain):
        keychain.clear()


class TestExpiry:
    def test_a_credential_past_its_date_is_not_usable(self, keychain):
        keychain.save(stored(expires_on=date(2026, 8, 23)))

        loaded = keychain.load()

        assert loaded is not None and loaded.is_expired(TODAY)
        assert loaded.usable_on(TODAY) is False

    def test_a_credential_expiring_today_still_works_today(self):
        assert stored(expires_on=TODAY).is_expired(TODAY) is False

    def test_it_counts_the_days_left(self):
        assert stored(expires_on=date(2026, 8, 29)).days_left(TODAY) == 5

    def test_a_date_that_is_not_a_date_is_refused_at_entry(self):
        with pytest.raises(ValueError):
            StoredCredential.of(
                email="reviewer@streamstech.com", token="t", expires_on="next tuesday"
            )

    def test_an_expiry_in_the_past_is_refused_at_entry(self):
        with pytest.raises(ValueError) as caught:
            StoredCredential.of(
                email="reviewer@streamstech.com",
                token="t",
                expires_on="2026-08-01",
                today=TODAY,
            )

        assert "past" in str(caught.value).lower()


class TestAnUnavailableKeychain:
    """No file fallback. Ever. ADR-0003 says the server refuses rather than downgrades."""

    @pytest.fixture(autouse=True)
    def _no_keyring(self):
        previous = keyring.get_keyring()
        keyring.set_keyring(FailingKeyring())
        yield
        keyring.set_keyring(previous)

    def test_saving_fails_loudly(self, tmp_path):
        with pytest.raises(KeychainUnavailable) as caught:
            Keychain().save(stored())

        assert "keychain" in str(caught.value).lower()
        assert list(tmp_path.iterdir()) == [], "a credential must never land in a file"

    def test_loading_fails_loudly_rather_than_reading_a_file(self):
        with pytest.raises(KeychainUnavailable):
            Keychain().load()


class TestAnEntryWeCannotRead:
    """A blob left by an older version, or a half-written one, must not wedge startup."""

    def test_it_reads_as_no_credential_so_setup_can_replace_it(self, keychain, memory_keyring):
        memory_keyring.secrets[(SERVICE, ACCOUNT)] = "{not json"

        assert keychain.load() is None

    def test_a_blob_missing_a_field_reads_as_no_credential(self, keychain, memory_keyring):
        memory_keyring.secrets[(SERVICE, ACCOUNT)] = '{"email": "reviewer@streamstech.com"}'

        assert keychain.load() is None


class TestTheTokenStaysOutOfLogs:
    def test_repr_of_a_stored_credential_redacts_the_token(self):
        assert "a-real-token" not in repr(stored())


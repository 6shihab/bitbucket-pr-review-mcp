"""The credential a container is given, and everything it is not allowed to do.

ADR-0007 permits this only because it is a decision rather than a degradation. The tests
that matter here are the negative ones: nothing falls back to the environment, nothing
writes to it, and setup cannot be opened in a process with nowhere to save.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from bitbucket_pr_review_mcp.credentials import CredentialError
from bitbucket_pr_review_mcp.environment import (
    EMAIL_ENV,
    EXPIRY_ENV,
    TOKEN_ENV,
    EnvironmentStore,
    NotStorable,
    SetupUnavailable,
)
from bitbucket_pr_review_mcp.gate import CredentialGate

TODAY = date(2026, 8, 24)


@pytest.fixture
def supplied(monkeypatch):
    monkeypatch.setenv(EMAIL_ENV, "reviewer@streamstech.com")
    monkeypatch.setenv(TOKEN_ENV, "a-real-token")
    monkeypatch.delenv(EXPIRY_ENV, raising=False)
    return EnvironmentStore.configured(today=TODAY)


class TestWhenItApplies:
    def test_both_variables_together_are_the_opt_in(self, supplied):
        assert supplied is not None
        assert supplied.load().credential.email == "reviewer@streamstech.com"

    @pytest.mark.parametrize("present", [EMAIL_ENV, TOKEN_ENV, None])
    def test_half_a_credential_is_not_an_opt_in(self, monkeypatch, present):
        monkeypatch.delenv(EMAIL_ENV, raising=False)
        monkeypatch.delenv(TOKEN_ENV, raising=False)
        if present:
            monkeypatch.setenv(present, "value")

        assert EnvironmentStore.configured(today=TODAY) is None

    def test_nothing_in_the_environment_means_nothing_happens(self, monkeypatch):
        monkeypatch.delenv(EMAIL_ENV, raising=False)
        monkeypatch.delenv(TOKEN_ENV, raising=False)

        assert EnvironmentStore.configured(today=TODAY) is None

    def test_a_bitbucket_username_is_refused_the_same_as_anywhere_else(self, monkeypatch):
        monkeypatch.setenv(EMAIL_ENV, "anwar")
        monkeypatch.setenv(TOKEN_ENV, "a-real-token")

        with pytest.raises(CredentialError):
            EnvironmentStore.configured(today=TODAY).load()


class TestTheExpiry:
    def test_a_stated_expiry_is_used(self, monkeypatch, supplied):
        monkeypatch.setenv(EXPIRY_ENV, "2027-03-01")

        assert EnvironmentStore.configured(today=TODAY).expires_on == date(2027, 3, 1)

    def test_an_unstated_expiry_is_assumed_rather_than_absent(self, supplied):
        assert supplied.expires_on == TODAY + timedelta(days=365)

    def test_a_date_that_is_not_a_date_is_refused_at_startup(self, monkeypatch, supplied):
        monkeypatch.setenv(EXPIRY_ENV, "next tuesday")

        with pytest.raises(CredentialError) as caught:
            EnvironmentStore.configured(today=TODAY)

        assert EXPIRY_ENV in str(caught.value)


class TestItIsReadOnly:
    def test_saving_is_refused(self, supplied):
        with pytest.raises(NotStorable) as caught:
            supplied.save(supplied.load())

        assert "nowhere to store" in str(caught.value)
        assert TOKEN_ENV in str(caught.value)

    def test_clearing_is_refused(self, supplied):
        with pytest.raises(NotStorable):
            supplied.clear()

    def test_setup_cannot_be_opened(self):
        with pytest.raises(CredentialError) as caught:
            SetupUnavailable().start(lambda stored: None)

        assert "no keychain here to save into" in str(caught.value)
        assert "bb-pr-mcp --setup" in str(caught.value)

    def test_stopping_a_listener_that_never_started_is_harmless(self):
        assert SetupUnavailable().stop() is None


class TestThroughTheGate:
    def test_the_gate_reads_it_like_any_other_store(self, supplied):
        gate = CredentialGate(supplied, SetupUnavailable(), today=lambda: TODAY)

        assert gate.current().email == "reviewer@streamstech.com"

    def test_a_401_cannot_reopen_setup_in_a_container(self, supplied):
        """The three facts that open setup still happen; none can be fixed from in here."""
        gate = CredentialGate(supplied, SetupUnavailable(), today=lambda: TODAY)
        gate.report_unauthorized()

        with pytest.raises(CredentialError) as caught:
            gate.current()

        assert "restart" in str(caught.value)

    def test_an_expired_credential_says_where_to_fix_it(self, monkeypatch):
        monkeypatch.setenv(EMAIL_ENV, "reviewer@streamstech.com")
        monkeypatch.setenv(TOKEN_ENV, "a-real-token")
        monkeypatch.setenv(EXPIRY_ENV, "2026-08-01")
        gate = CredentialGate(
            EnvironmentStore.configured(today=TODAY), SetupUnavailable(), today=lambda: TODAY
        )

        with pytest.raises(CredentialError) as caught:
            gate.current()

        assert EMAIL_ENV in str(caught.value)


class TestTheKeychainStillSaysWhatToDo:
    def test_an_unavailable_keychain_names_the_container_option(self, monkeypatch):
        import keyring
        from keyring.backends.fail import Keyring as FailingKeyring

        from bitbucket_pr_review_mcp.keychain import Keychain, KeychainUnavailable

        previous = keyring.get_keyring()
        keyring.set_keyring(FailingKeyring())
        try:
            with pytest.raises(KeychainUnavailable) as caught:
                Keychain().load()
        finally:
            keyring.set_keyring(previous)

        assert "In a container there is no keychain at all" in str(caught.value)
        assert "ADR-0007" in str(caught.value)

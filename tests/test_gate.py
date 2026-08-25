"""When the setup listener opens, and — the part that matters — when it does not.

ADR-0004 permits three triggers, all observed: no credential, an expired one, a real 401.
A listener that could be summoned any other way would let text inside a pull request
raise a credential form at the moment the Reviewer is least suspicious of one.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from bitbucket_pr_review_mcp.credentials import Credential, StoredCredential
from bitbucket_pr_review_mcp.gate import RENEW_COMMAND, CredentialGate, SetupRequired

from .conftest import RecordingSetup

TODAY = date(2026, 8, 24)
URL = RecordingSetup.URL


def stored(expires_on: date = date(2027, 1, 1)) -> StoredCredential:
    return StoredCredential(
        credential=Credential(email="reviewer@streamstech.com", token="a-real-token"),
        expires_on=expires_on,
    )


def _expired(on: date) -> StoredCredential:
    """Straight past the entry check: only a credential already in the keychain can be
    expired, so this is the one place that builds one without asking `of`."""
    return StoredCredential(credential=stored().credential, expires_on=on)


@pytest.fixture
def gate(keychain, setup_listener) -> CredentialGate:
    """A gate over an empty keychain, on a fixed day. `setup` records what it is asked."""
    return CredentialGate(keychain, setup_listener, today=lambda: TODAY)


class TestWithNoCredential:
    def test_it_refuses_with_the_setup_url(self, gate, setup_listener):
        with pytest.raises(SetupRequired) as caught:
            gate.current()

        assert URL in str(caught.value)
        assert setup_listener.starts == 1

    def test_every_refusal_asks_for_the_url_again(self, gate, setup_listener):
        for _ in range(3):
            with pytest.raises(SetupRequired) as caught:
                gate.current()
            assert URL in str(caught.value)

        # Idempotence lives in the listener, which returns the link it already handed out.
        assert setup_listener.starts == 3


class TestWithAGoodCredential:
    def test_it_hands_over_the_credential(self, gate, keychain):
        keychain.save(stored())

        assert gate.current().email == "reviewer@streamstech.com"

    def test_no_listener_exists_while_a_credential_is_usable(self, gate, keychain, setup_listener):
        keychain.save(stored())

        gate.current()

        assert setup_listener.starts == 0


class TestWithAnExpiredCredential:
    def test_it_reopens_setup_and_says_when_it_lapsed(self, gate, keychain, setup_listener):
        keychain.save(_expired(date(2026, 8, 1)))

        with pytest.raises(SetupRequired) as caught:
            gate.current()

        assert "2026-08-01" in str(caught.value)
        assert setup_listener.starts == 1


class TestAfterA401:
    def test_it_reopens_setup(self, gate, keychain, setup_listener):
        keychain.save(stored())
        gate.current()

        gate.report_unauthorized()

        with pytest.raises(SetupRequired) as caught:
            gate.current()
        assert "rejected" in str(caught.value).lower()
        assert setup_listener.starts == 1

    def test_the_stored_credential_is_not_destroyed(self, gate, keychain):
        keychain.save(stored())

        gate.report_unauthorized()

        assert keychain.load() is not None, "a 401 is not proof the token should be deleted"

    def test_completing_setup_clears_the_rejection(self, gate, keychain, setup_listener):
        keychain.save(stored())
        gate.report_unauthorized()
        with pytest.raises(SetupRequired):
            gate.current()

        setup_listener.on_saved(stored())

        assert gate.current().email == "reviewer@streamstech.com"


class TestTheExpiryWarning:
    @pytest.mark.parametrize("days,expected", [(30, False), (8, False), (7, True), (1, True)])
    def test_it_warns_only_inside_the_last_week(self, gate, keychain, days, expected):
        keychain.save(stored(expires_on=TODAY + timedelta(days=days)))

        assert (gate.expiry_warning() is not None) is expected

    def test_the_warning_names_the_command_that_renews_it(self, gate, keychain):
        keychain.save(stored(expires_on=TODAY + timedelta(days=3)))

        assert RENEW_COMMAND in gate.expiry_warning()

    def test_an_expired_credential_warns_too(self, gate, keychain):
        keychain.save(_expired(date(2026, 1, 1)))

        assert "expired" in gate.expiry_warning()

    def test_nothing_to_warn_about_when_there_is_no_credential(self, gate):
        assert gate.expiry_warning() is None


class TestReadingThroughOnASharedServer:
    """`hold=False`. The per-device gate holds the credential because every keychain read
    on macOS is a potential prompt; a database row is neither, and holding it would mean
    an operator's revocation took effect at the next restart."""

    def test_it_notices_a_credential_that_has_been_removed(self, keychain, setup_listener):
        from datetime import date

        from bitbucket_pr_review_mcp.credentials import Credential, StoredCredential

        keychain.save(
            StoredCredential(
                credential=Credential(email="alice@streamstech.com", token="hers"),
                expires_on=date(2099, 1, 1),
            )
        )
        gate = CredentialGate(keychain, setup_listener, hold=False)
        assert gate.current().email == "alice@streamstech.com"

        keychain.clear()

        from bitbucket_pr_review_mcp.credentials import CredentialError

        with pytest.raises(CredentialError):
            gate.current()

    def test_it_notices_a_credential_that_has_been_replaced(self, keychain, setup_listener):
        from datetime import date

        from bitbucket_pr_review_mcp.credentials import Credential, StoredCredential

        def store(email: str) -> None:
            keychain.save(
                StoredCredential(
                    credential=Credential(email=email, token="t"),
                    expires_on=date(2099, 1, 1),
                )
            )

        store("alice@streamstech.com")
        gate = CredentialGate(keychain, setup_listener, hold=False)
        gate.current()

        store("alice.new@streamstech.com")

        assert gate.current().email == "alice.new@streamstech.com"

    def test_replacing_it_forgets_what_was_cached_against_the_old_one(
        self, keychain, setup_listener
    ):
        """Chiefly "which Bitbucket account are we?" — wrong now, and wrong in the
        direction that edits somebody else's comment."""
        from datetime import date

        from bitbucket_pr_review_mcp.credentials import Credential, StoredCredential

        forgotten: list[str] = []

        def store(email: str) -> None:
            keychain.save(
                StoredCredential(
                    credential=Credential(email=email, token="t"),
                    expires_on=date(2099, 1, 1),
                )
            )

        store("alice@streamstech.com")
        gate = CredentialGate(keychain, setup_listener, hold=False)
        gate.when_credential_changes(lambda: forgotten.append("yes"))
        gate.current()

        store("alice.new@streamstech.com")
        gate.current()

        assert forgotten == ["yes"]

    def test_reading_the_same_credential_twice_forgets_nothing(
        self, keychain, setup_listener, credential
    ):
        from datetime import date

        from bitbucket_pr_review_mcp.credentials import StoredCredential

        keychain.save(StoredCredential(credential=credential, expires_on=date(2099, 1, 1)))
        gate = CredentialGate(keychain, setup_listener, hold=False)
        forgotten: list[str] = []
        gate.when_credential_changes(lambda: forgotten.append("yes"))

        gate.current()
        gate.current()
        gate.current()

        assert forgotten == []

    def test_the_per_device_gate_still_holds(self, keychain, setup_listener, credential):
        """Unchanged, and deliberately: a keychain read can prompt, and a tool call that
        asks the Reviewer to authorise something is a habit worth not building."""
        from datetime import date

        from bitbucket_pr_review_mcp.credentials import StoredCredential

        keychain.save(StoredCredential(credential=credential, expires_on=date(2099, 1, 1)))
        gate = CredentialGate(keychain, setup_listener)
        gate.current()

        keychain.clear()

        assert gate.current().email == credential.email

"""What an operator can do to somebody else's stored credential.

Two commands, deliberately not tools: a pull request description must not be able to talk
a Caller into revoking a colleague. The interesting part of `--revoke` is not what it
does — one row, deleted — but what it says it has *not* done.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from bitbucket_pr_review_mcp.__main__ import _run_revoke, _run_who, configure_logging
from bitbucket_pr_review_mcp.credentials import Credential, StoredCredential
from bitbucket_pr_review_mcp.settings import Settings
from bitbucket_pr_review_mcp.tokens import person_id
from bitbucket_pr_review_mcp.vault import KEY_ENV, CredentialVault, VaultKey

ISSUER = "https://keycloak.streamstech.com/realms/streamstech"

ALICE = person_id(ISSUER, "alice-sub")
BOB = person_id(ISSUER, "bob-sub")

ALICE_TOKEN = "ATATT-alice-should-never-be-printed"
BOB_TOKEN = "ATATT-bob-should-never-be-printed"


@pytest.fixture
def settings(tmp_path, monkeypatch, capsys) -> Settings:
    # `capsys` first: loguru binds to whatever `sys.stderr` is when the sink is
    # added, so configuring logging before capture starts writes past the test.
    key = VaultKey.generate()
    monkeypatch.setenv(KEY_ENV, key.exported())
    configure_logging("INFO")
    return Settings(vault_file=tmp_path / "credentials.sqlite3")


@pytest.fixture
def vault(settings):
    store = CredentialVault.at(settings.vault_file, VaultKey.required())
    yield store
    store.close()


def connect(vault, person: str, email: str, token: str, *, expires_on: date | None = None):
    vault.save(
        person,
        StoredCredential(
            credential=Credential(email=email, token=token),
            expires_on=expires_on or (date.today() + timedelta(days=200)),
        ),
    )


def said(capsys) -> str:
    return capsys.readouterr().err


class TestWhoHasConnected:
    def test_an_empty_server_says_so(self, settings, vault, capsys):
        assert _run_who(settings) == 0
        assert "Nobody has connected" in said(capsys)

    def test_it_names_everybody(self, settings, vault, capsys):
        connect(vault, ALICE, "alice@streamstech.com", ALICE_TOKEN)
        connect(vault, BOB, "bob@streamstech.com", BOB_TOKEN)

        assert _run_who(settings) == 0

        spoken = said(capsys)
        assert "alice@streamstech.com" in spoken
        assert "bob@streamstech.com" in spoken

    def test_it_shows_no_tokens(self, settings, vault, capsys):
        """It decrypts to answer, and then prints everything except the one field worth
        decrypting for."""
        connect(vault, ALICE, "alice@streamstech.com", ALICE_TOKEN)
        connect(vault, BOB, "bob@streamstech.com", BOB_TOKEN)

        _run_who(settings)

        spoken = said(capsys)
        assert ALICE_TOKEN not in spoken
        assert BOB_TOKEN not in spoken

    def test_it_says_when_a_token_runs_out(self, settings, vault, capsys):
        connect(
            vault,
            ALICE,
            "alice@streamstech.com",
            ALICE_TOKEN,
            expires_on=date.today() + timedelta(days=3),
        )

        _run_who(settings)

        assert "3 days left" in said(capsys)

    def test_an_expired_token_is_called_expired(self, settings, vault, capsys):
        connect(
            vault,
            ALICE,
            "alice@streamstech.com",
            ALICE_TOKEN,
            expires_on=date.today() - timedelta(days=1),
        )

        _run_who(settings)

        assert "expired" in said(capsys)

    def test_a_row_it_cannot_read_is_reported_rather_than_hidden(
        self, settings, vault, capsys, monkeypatch
    ):
        """One row encrypted under a key this server no longer has must not make the
        operator's view of everybody else disappear."""
        import sqlite3

        connect(vault, ALICE, "alice@streamstech.com", ALICE_TOKEN)
        connect(vault, BOB, "bob@streamstech.com", BOB_TOKEN)
        vault.close()

        damaged = sqlite3.connect(settings.vault_file)
        damaged.execute("UPDATE credentials SET sealed = ? WHERE person = ?", (b"rubbish", ALICE))
        damaged.commit()
        damaged.close()

        assert _run_who(settings) == 0

        spoken = said(capsys)
        assert "UNREADABLE" in spoken
        assert "bob@streamstech.com" in spoken

    def test_without_a_key_it_refuses_rather_than_guessing(self, settings, monkeypatch, capsys):
        monkeypatch.delenv(KEY_ENV, raising=False)

        assert _run_who(settings) == 2


class TestRevoking:
    def test_by_email(self, settings, vault, capsys):
        connect(vault, ALICE, "alice@streamstech.com", ALICE_TOKEN)

        assert _run_revoke(settings, "alice@streamstech.com") == 0
        assert vault.load(ALICE) is None

    def test_by_enough_of_the_opaque_id(self, settings, vault):
        connect(vault, ALICE, "alice@streamstech.com", ALICE_TOKEN)

        assert _run_revoke(settings, ALICE[:12]) == 0
        assert vault.load(ALICE) is None

    def test_by_the_whole_id(self, settings, vault):
        connect(vault, ALICE, "alice@streamstech.com", ALICE_TOKEN)

        assert _run_revoke(settings, ALICE) == 0

    def test_it_leaves_everybody_else_connected(self, settings, vault):
        connect(vault, ALICE, "alice@streamstech.com", ALICE_TOKEN)
        connect(vault, BOB, "bob@streamstech.com", BOB_TOKEN)

        _run_revoke(settings, "alice@streamstech.com")

        assert vault.load(BOB) is not None

    def test_somebody_who_is_not_here(self, settings, vault, capsys):
        assert _run_revoke(settings, "nobody@streamstech.com") == 1
        assert "--who" in said(capsys)

    def test_a_prefix_too_short_to_mean_anything(self, settings, vault):
        connect(vault, ALICE, "alice@streamstech.com", ALICE_TOKEN)

        assert _run_revoke(settings, ALICE[:4]) == 1

    def test_an_ambiguous_name_refuses_and_lists_them(self, settings, vault, capsys):
        """Two Keycloak accounts can connect the same Atlassian address. Guessing which
        one to revoke is worse than asking."""
        connect(vault, ALICE, "shared@streamstech.com", ALICE_TOKEN)
        connect(vault, BOB, "shared@streamstech.com", BOB_TOKEN)

        assert _run_revoke(settings, "shared@streamstech.com") == 2
        assert vault.load(ALICE) is not None
        assert vault.load(BOB) is not None

    def test_it_prints_no_token(self, settings, vault, capsys):
        connect(vault, ALICE, "alice@streamstech.com", ALICE_TOKEN)

        _run_revoke(settings, "alice@streamstech.com")

        assert ALICE_TOKEN not in said(capsys)


class TestWhatRevokingDoesNotDo:
    """Three places hold something after somebody leaves, and this command owns one.
    A command that implied otherwise would get an offboarding checklist ticked while the
    person still had access."""

    @pytest.fixture
    def spoken(self, settings, vault, capsys) -> str:
        connect(vault, ALICE, "alice@streamstech.com", ALICE_TOKEN)
        _run_revoke(settings, "alice@streamstech.com")
        return said(capsys)

    def test_it_says_they_can_still_sign_in(self, spoken):
        assert "Keycloak" in spoken

    def test_it_says_the_atlassian_token_still_works(self, spoken):
        assert "still exists" in spoken
        assert "id.atlassian.com" in spoken

    def test_it_does_not_claim_to_have_finished_the_job(self, spoken):
        assert "That is all this command can do" in spoken

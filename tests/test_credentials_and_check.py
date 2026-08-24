"""The credential, and the --check command that proves it works.

The email trap is the reason most of this exists. Bitbucket Cloud's Basic authentication
username is the Atlassian account email; a Bitbucket username or the token's name gets a
401 with an empty body and nothing to diagnose. So a wrong identifier fails here, at
construction, rather than three days later during a review.
"""

from __future__ import annotations

import httpx
import pytest

from bitbucket_pr_review_mcp.__main__ import _run_check, _startup_checks
from bitbucket_pr_review_mcp.credentials import Credential, CredentialError
from bitbucket_pr_review_mcp.settings import Settings

from . import fixtures

GOOD_SCOPES = "read:repository:bitbucket, write:pullrequest:bitbucket"


def transport_for(wire):
    return lambda: httpx.AsyncClient(
        transport=wire.transport(), base_url="https://api.bitbucket.org"
    )


def whoami(scopes: str | None = GOOD_SCOPES) -> httpx.Response:
    headers = {"x-oauth-scopes": scopes} if scopes is not None else {}
    return httpx.Response(200, json=fixtures.current_user(), headers=headers)


class TestTheEmailTrap:
    @pytest.mark.parametrize("identifier", ["anwar", "my-review-token", "streamstech"])
    def test_refuses_anything_that_is_not_an_email(self, identifier):
        with pytest.raises(CredentialError) as caught:
            Credential(email=identifier, token="t")

        assert "email" in str(caught.value).lower()

    def test_accepts_an_atlassian_account_email(self):
        credential = Credential(email="reviewer@streamstech.com", token="t")

        assert credential.email == "reviewer@streamstech.com"

    def test_refuses_an_empty_token(self):
        with pytest.raises(CredentialError):
            Credential(email="reviewer@streamstech.com", token="   ")


class TestTheTokenStaysOutOfSight:
    def test_repr_redacts_the_token(self):
        credential = Credential(email="reviewer@streamstech.com", token="s3cr3t-token")

        assert "s3cr3t-token" not in repr(credential)
        assert "redacted" in repr(credential)

    def test_the_authorization_header_is_basic(self):
        credential = Credential(email="reviewer@streamstech.com", token="t")

        assert credential.authorization_header().startswith("Basic ")


class TestTheCheckCommand:
    async def test_succeeds_when_the_credential_works(self, wire, allowlist, gate):
        wire.will_return(whoami())

        code = await _run_check(Settings(), allowlist, gate, http_factory=transport_for(wire))

        assert code == 0

    async def test_fails_with_a_nonzero_code_when_the_credential_is_rejected(
        self, wire, allowlist, gate
    ):
        wire.will_return(httpx.Response(401, text=""))

        code = await _run_check(Settings(), allowlist, gate, http_factory=transport_for(wire))

        assert code == 1

    async def test_asks_bitbucket_who_it_is(self, wire, allowlist, gate):
        wire.will_return(whoami())

        await _run_check(Settings(), allowlist, gate, http_factory=transport_for(wire))

        assert wire.last.url.path == "/2.0/user"

    async def test_reports_a_missing_credential_rather_than_calling_bitbucket(
        self, wire, allowlist, empty_gate
    ):
        code = await _run_check(Settings(), allowlist, empty_gate, http_factory=transport_for(wire))

        assert code == 1
        assert not wire.called

    async def test_refuses_a_token_that_can_do_more_than_review(self, wire, allowlist, gate):
        wire.will_return(whoami(f"{GOOD_SCOPES}, admin:repository:bitbucket"))

        code = await _run_check(Settings(), allowlist, gate, http_factory=transport_for(wire))

        assert code == 2

    async def test_refuses_a_token_that_cannot_write_a_comment(self, wire, allowlist, gate):
        wire.will_return(whoami("read:repository:bitbucket"))

        code = await _run_check(Settings(), allowlist, gate, http_factory=transport_for(wire))

        assert code == 2


class TestStartup:
    """Startup is where a Reviewer finds out, rather than three tool calls later."""

    def test_refuses_to_run_on_an_over_broad_token(self, wire, allowlist, gate):
        wire.will_return(whoami(f"{GOOD_SCOPES}, write:repository:bitbucket"))

        with pytest.raises(SystemExit) as caught:
            _startup_checks(Settings(), allowlist, gate, http_factory=transport_for(wire))

        assert caught.value.code == 2

    def test_starts_on_a_correctly_scoped_token(self, wire, allowlist, gate):
        wire.will_return(whoami())

        _startup_checks(Settings(), allowlist, gate, http_factory=transport_for(wire))

    def test_starts_without_a_credential_so_the_tools_can_say_where_setup_is(
        self, wire, allowlist, empty_gate, setup_listener
    ):
        _startup_checks(Settings(), allowlist, empty_gate, http_factory=transport_for(wire))

        assert setup_listener.starts == 1
        assert not wire.called

    def test_bitbucket_being_unreachable_is_not_treated_as_a_bad_token(
        self, wire, allowlist, gate
    ):
        wire.will_return(httpx.Response(503, text="down"))

        _startup_checks(Settings(), allowlist, gate, http_factory=transport_for(wire))


class TestTheSetupCommand:
    def test_it_returns_zero_once_a_credential_is_in_the_keychain(self, gate, setup_listener):
        from bitbucket_pr_review_mcp.__main__ import _run_setup

        assert _run_setup(gate) == 0
        assert setup_listener.starts == 1

    def test_it_gives_up_and_says_so_when_setup_is_never_completed(
        self, empty_gate, setup_listener, monkeypatch
    ):
        from bitbucket_pr_review_mcp import __main__ as entrypoint

        monkeypatch.setattr(entrypoint, "SETUP_WAIT_SECONDS", 0.1)

        assert entrypoint._run_setup(empty_gate) == 1
        assert setup_listener.stops == 1, "an abandoned listener does not stay open"

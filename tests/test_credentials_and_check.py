"""The credential, and the --check command that proves it works.

The email trap is the reason most of this exists. Bitbucket Cloud's Basic authentication
username is the Atlassian account email; a Bitbucket username or the token's name gets a
401 with an empty body and nothing to diagnose. So a wrong identifier fails here, at
construction, rather than three days later during a review.
"""

from __future__ import annotations

import httpx
import pytest

from bitbucket_pr_review_mcp.__main__ import _run_check
from bitbucket_pr_review_mcp.credentials import (
    EMAIL_ENV,
    TOKEN_ENV,
    Credential,
    CredentialError,
    from_environment,
)
from bitbucket_pr_review_mcp.settings import Settings

from . import fixtures


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


class TestReadingTheEnvironment:
    def test_reads_both_variables(self, monkeypatch):
        monkeypatch.setenv(EMAIL_ENV, "reviewer@streamstech.com")
        monkeypatch.setenv(TOKEN_ENV, "a-token")

        assert from_environment().email == "reviewer@streamstech.com"

    @pytest.mark.parametrize("present", [EMAIL_ENV, TOKEN_ENV, None])
    def test_says_what_to_set_when_something_is_missing(self, monkeypatch, present):
        monkeypatch.delenv(EMAIL_ENV, raising=False)
        monkeypatch.delenv(TOKEN_ENV, raising=False)
        if present:
            monkeypatch.setenv(present, "value")

        with pytest.raises(CredentialError) as caught:
            from_environment()

        assert EMAIL_ENV in str(caught.value) and TOKEN_ENV in str(caught.value)


class TestTheCheckCommand:
    async def test_succeeds_when_the_credential_works(self, wire, allowlist, credential):
        wire.will_return(httpx.Response(200, json=fixtures.current_user()))

        code = await _run_check(
            Settings(),
            allowlist,
            credential,
            http_factory=lambda: httpx.AsyncClient(
                transport=wire.transport(), base_url="https://api.bitbucket.org"
            ),
        )

        assert code == 0

    async def test_fails_with_a_nonzero_code_when_the_credential_is_rejected(
        self, wire, allowlist, credential
    ):
        wire.will_return(httpx.Response(401, text=""))

        code = await _run_check(
            Settings(),
            allowlist,
            credential,
            http_factory=lambda: httpx.AsyncClient(
                transport=wire.transport(), base_url="https://api.bitbucket.org"
            ),
        )

        assert code == 1

    async def test_asks_bitbucket_who_it_is(self, wire, allowlist, credential):
        wire.will_return(httpx.Response(200, json=fixtures.current_user()))

        await _run_check(
            Settings(),
            allowlist,
            credential,
            http_factory=lambda: httpx.AsyncClient(
                transport=wire.transport(), base_url="https://api.bitbucket.org"
            ),
        )

        assert wire.last.url.path == "/2.0/user"

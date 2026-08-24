"""Judging what a pasted token can do, from what Bitbucket says it granted."""

from __future__ import annotations

import pytest

from bitbucket_pr_review_mcp.scopes import review_scopes

GOOD_SCOPES = (
    "read:user:bitbucket, read:repository:bitbucket, "
    "read:pullrequest:bitbucket, write:pullrequest:bitbucket"
)


def verdict(header):
    return review_scopes(header)


class TestAGoodToken:
    def test_the_granular_trio_is_acceptable_and_complete(self):
        result = verdict(GOOD_SCOPES)

        assert result.acceptable and result.complete and result.verified

    def test_the_older_app_password_spelling_is_accepted_too(self):
        result = verdict("account, repository, pullrequest:write")

        assert result.acceptable and result.complete

    def test_extra_read_only_scopes_are_tolerated(self):
        result = verdict(f"{GOOD_SCOPES}, read:account, read:workspace:bitbucket")

        assert result.acceptable


class TestATokenWithoutTheUserScope:
    """The one a real token was missing: Bitbucket answers /2.0/user with 403, so the
    server cannot say who it posts as or recognise its own comments (ticket 05)."""

    def test_it_is_incomplete_and_says_which_scope_to_add(self):
        result = verdict("read:repository:bitbucket, write:pullrequest:bitbucket")

        assert not result.complete
        assert "read:user:bitbucket" in result.shortfall()


class TestATokenThatCanWriteButNotRead:
    """Granular scopes do not nest. A token with write:pullrequest and no
    read:pullrequest authenticates fine and then 403s on the first thing a review does."""

    def test_writing_a_pull_request_does_not_imply_reading_one(self):
        result = verdict(
            "read:user:bitbucket, read:repository:bitbucket, write:pullrequest:bitbucket"
        )

        assert not result.complete
        assert "read:pullrequest:bitbucket" in result.shortfall()

    def test_the_older_app_password_scope_did_imply_it(self):
        result = verdict("account, repository, pullrequest:write")

        assert result.complete


class TestATokenThatIsTooPowerful:
    @pytest.mark.parametrize(
        "scope",
        [
            "write:repository:bitbucket",
            "admin:repository:bitbucket",
            "delete:repository:bitbucket",
            "write:workspace:bitbucket",
            "pipeline:write",
            "webhook",
        ],
    )
    def test_anything_beyond_pull_request_writing_is_excessive(self, scope):
        result = verdict(f"{GOOD_SCOPES}, {scope}")

        assert not result.acceptable
        assert scope in result.excessive
        assert scope in result.refusal()

    def test_the_refusal_names_the_scopes_to_grant_instead(self):
        result = verdict(f"{GOOD_SCOPES}, admin:repository")

        assert "write:pullrequest:bitbucket" in result.refusal()
        assert "id.atlassian.com" in result.refusal()

    def test_an_unrecognised_scope_fails_closed(self):
        result = verdict(f"{GOOD_SCOPES}, teleport:all")

        assert not result.acceptable


class TestATokenThatIsTooWeak:
    def test_a_read_only_token_is_incomplete(self):
        result = verdict(
            "read:user:bitbucket, read:repository:bitbucket, read:pullrequest:bitbucket"
        )

        assert result.acceptable
        assert not result.complete
        assert "write:pullrequest:bitbucket" in result.shortfall()


class TestWhenBitbucketSaysNothing:
    def test_an_absent_header_verifies_nothing_and_refuses_nothing(self):
        result = verdict(None)

        assert result.verified is False
        assert result.acceptable, "we cannot refuse a token on evidence we do not have"

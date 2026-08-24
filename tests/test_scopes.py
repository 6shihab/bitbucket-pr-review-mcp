"""Judging what a pasted token can do, from what Bitbucket says it granted."""

from __future__ import annotations

import pytest

from bitbucket_pr_review_mcp.scopes import review_scopes


def verdict(header):
    return review_scopes(header)


class TestAGoodToken:
    def test_the_granular_pair_is_acceptable_and_complete(self):
        result = verdict("read:repository:bitbucket, write:pullrequest:bitbucket")

        assert result.acceptable and result.complete and result.verified

    def test_the_older_app_password_spelling_is_accepted_too(self):
        result = verdict("repository, pullrequest:write")

        assert result.acceptable and result.complete

    def test_extra_read_only_scopes_are_tolerated(self):
        result = verdict(
            "read:account, read:repository:bitbucket, read:user:bitbucket, "
            "write:pullrequest:bitbucket"
        )

        assert result.acceptable


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
        result = verdict(f"read:repository:bitbucket, write:pullrequest:bitbucket, {scope}")

        assert not result.acceptable
        assert scope in result.excessive
        assert scope in result.refusal()

    def test_the_refusal_names_the_scopes_to_grant_instead(self):
        result = verdict("read:repository:bitbucket, write:pullrequest:bitbucket, admin:repository")

        assert "write:pullrequest:bitbucket" in result.refusal()
        assert "id.atlassian.com" in result.refusal()

    def test_an_unrecognised_scope_fails_closed(self):
        result = verdict("read:repository:bitbucket, write:pullrequest:bitbucket, teleport:all")

        assert not result.acceptable


class TestATokenThatIsTooWeak:
    def test_a_read_only_token_is_incomplete(self):
        result = verdict("read:repository:bitbucket")

        assert result.acceptable
        assert not result.complete
        assert "write:pullrequest:bitbucket" in result.shortfall()


class TestWhenBitbucketSaysNothing:
    def test_an_absent_header_verifies_nothing_and_refuses_nothing(self):
        result = verdict(None)

        assert result.verified is False
        assert result.acceptable, "we cannot refuse a token on evidence we do not have"

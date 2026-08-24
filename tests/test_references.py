"""Parsing the string a Caller uses to name a Pull Request.

Both accepted forms must land on the same Pull Request, and anything else must be
refused with an error that names both forms — a Caller recovers from an error that
teaches, and guesses at one that does not.
"""

import pytest

from bitbucket_pr_review_mcp.references import InvalidReference, PullRequestRef


class TestFullUrls:
    def test_reads_a_pull_request_url(self):
        ref = PullRequestRef.parse("https://bitbucket.org/streamstech/db-explorer/pull-requests/42")

        assert ref.workspace == "streamstech"
        assert ref.repo == "db-explorer"
        assert ref.pull_request_id == 42

    def test_reads_a_url_with_a_trailing_path_segment(self):
        # Bitbucket appends /diff, /commits and friends when you click through the tabs,
        # and a human pasting from the address bar brings them along.
        ref = PullRequestRef.parse(
            "https://bitbucket.org/streamstech/db-explorer/pull-requests/42/diff"
        )

        assert ref.pull_request_id == 42

    def test_reads_a_url_with_query_and_fragment(self):
        ref = PullRequestRef.parse(
            "https://bitbucket.org/streamstech/db-explorer/pull-requests/42?w=1#comment-9"
        )

        assert ref.pull_request_id == 42

    def test_ignores_scheme_and_www(self):
        ref = PullRequestRef.parse(
            "http://www.bitbucket.org/streamstech/db-explorer/pull-requests/7"
        )

        assert (ref.workspace, ref.repo, ref.pull_request_id) == ("streamstech", "db-explorer", 7)


class TestShorthand:
    def test_reads_the_workspace_repo_id_form(self):
        ref = PullRequestRef.parse("streamstech/db-explorer/42")

        assert (ref.workspace, ref.repo, ref.pull_request_id) == ("streamstech", "db-explorer", 42)

    def test_both_forms_name_the_same_pull_request(self):
        from_url = PullRequestRef.parse(
            "https://bitbucket.org/streamstech/db-explorer/pull-requests/42"
        )
        from_shorthand = PullRequestRef.parse("streamstech/db-explorer/42")

        assert from_url == from_shorthand


class TestRejection:
    @pytest.mark.parametrize(
        "bad",
        [
            "",
            "   ",
            "42",
            "streamstech/db-explorer",
            "streamstech/db-explorer/not-a-number",
            "https://github.com/streamstech/db-explorer/pull/42",
            "https://bitbucket.org/streamstech/db-explorer",
            "https://bitbucket.org/streamstech/db-explorer/pull-requests/0",
            "https://bitbucket.org/streamstech/db-explorer/pull-requests/-1",
        ],
    )
    def test_refuses_what_it_cannot_read(self, bad):
        with pytest.raises(InvalidReference):
            PullRequestRef.parse(bad)

    def test_the_error_names_both_accepted_forms(self):
        with pytest.raises(InvalidReference) as caught:
            PullRequestRef.parse("nonsense")

        message = str(caught.value)
        assert "pull-requests" in message, "should show the URL form"
        assert "workspace/repo/id" in message, "should show the shorthand form"

    def test_a_github_url_is_refused_by_host_not_by_shape(self):
        # This shape parses fine; it is the wrong host that disqualifies it. Worth its own
        # test because silently accepting it would send requests to the wrong API.
        with pytest.raises(InvalidReference) as caught:
            PullRequestRef.parse("https://github.com/streamstech/db-explorer/pull-requests/42")

        assert "bitbucket.org" in str(caught.value)


class TestIdentity:
    def test_carries_the_repository_it_belongs_to(self):
        ref = PullRequestRef.parse("streamstech/db-explorer/42")

        assert ref.repository.full_name == "streamstech/db-explorer"

    def test_renders_back_to_the_shorthand_form(self):
        ref = PullRequestRef.parse("https://bitbucket.org/streamstech/db-explorer/pull-requests/42")

        assert str(ref) == "streamstech/db-explorer/42"

"""Reading the repository around the change: metadata, files, directories, history.

ADR-0006 widened the read surface on purpose — a reviewer that cannot look past the diff
is a linter. The tests that matter here are the ones on the edges of that widening: a
path that climbs out of the repository, a ref that would split the URL, a repository
nobody allowlisted.
"""

from __future__ import annotations

import httpx
import pytest

from bitbucket_pr_review_mcp.commits import AmbiguousRequest, fetch_commits
from bitbucket_pr_review_mcp.guard import Forbidden
from bitbucket_pr_review_mcp.references import PullRequestRef, Repository
from bitbucket_pr_review_mcp.render import UNTRUSTED_OPEN
from bitbucket_pr_review_mcp.repositories import fetch_repository
from bitbucket_pr_review_mcp.source import UnreadablePath, fetch_directory, fetch_file

from . import fixtures

REPO = Repository("streamstech", "db-explorer")
UNLISTED = Repository("streamstech", "secret-payroll")
BASIS = fixtures.BASIS
FILE = "src/app/retry.py"


def json_response(payload, status=200):
    return httpx.Response(status, json=payload)


def text_response(body: str, content_type: str = "text/plain"):
    return httpx.Response(200, text=body, headers={"content-type": content_type})


class TestRepositoryMetadata:
    async def test_it_returns_the_default_branch(self, client, wire):
        wire.will_return(json_response(fixtures.repository()))

        found = await fetch_repository(client, REPO)

        assert found.default_branch == "main"
        assert found.repository == REPO

    async def test_a_repository_without_a_mainbranch_says_so_rather_than_guessing(
        self, client, wire
    ):
        payload = fixtures.repository()
        payload["mainbranch"] = None
        wire.will_return(json_response(payload))

        assert (await fetch_repository(client, REPO)).default_branch == "unknown"

    async def test_the_description_is_fenced_as_untrusted(self, client, wire):
        wire.will_return(json_response(fixtures.repository()))

        rendered = (await fetch_repository(client, REPO)).to_markdown()

        assert UNTRUSTED_OPEN in rendered

    async def test_an_unlisted_repository_never_reaches_the_network(self, client, wire):
        with pytest.raises(Forbidden):
            await fetch_repository(client, UNLISTED)

        assert not wire.called


class TestReadingAFile:
    async def test_it_reads_the_file_at_the_review_basis(self, client, wire):
        wire.will_return(text_response("def call_upstream():\n    return 1\n"))

        found = await fetch_file(client, REPO, FILE, BASIS)

        assert "call_upstream" in found.content
        assert wire.last.url.path == (
            f"/2.0/repositories/streamstech/db-explorer/src/{BASIS}/{FILE}"
        )

    async def test_the_rendered_file_carries_line_numbers_and_the_anchor_warning(
        self, client, wire
    ):
        wire.will_return(text_response("first\nsecond\n"))

        rendered = (await fetch_file(client, REPO, FILE, BASIS)).to_markdown(limit=10_000)

        assert "1 | first" in rendered
        assert "Anchor must still come from a diff hunk" in rendered

    async def test_a_long_file_says_it_was_truncated(self, client, wire):
        wire.will_return(text_response("x\n" * 5_000))

        rendered = (await fetch_file(client, REPO, FILE, BASIS)).to_markdown(limit=200)

        assert "Truncated" in rendered
        assert rendered.index("Truncated") < rendered.index(UNTRUSTED_OPEN)

    async def test_a_binary_file_is_not_dumped(self, client, wire):
        wire.will_return(text_response("\x00\x01binary"))

        found = await fetch_file(client, REPO, "assets/logo.png", BASIS)

        assert found.is_binary
        assert "logo.png" in found.to_markdown(limit=10_000)
        assert "binary" in found.to_markdown(limit=10_000).lower()

    async def test_asking_for_a_directory_says_which_tool_to_use(self, client, wire):
        wire.will_return(
            httpx.Response(
                200, json=fixtures.directory(), headers={"content-type": "application/json"}
            )
        )

        with pytest.raises(UnreadablePath) as caught:
            await fetch_file(client, REPO, "src/app", BASIS)

        assert "bitbucket_get_directory" in str(caught.value)

    @pytest.mark.parametrize("path", ["../secrets.env", "src/../../other/file.py", ".."])
    async def test_a_path_that_climbs_out_is_refused_before_the_network(
        self, client, wire, path
    ):
        with pytest.raises(UnreadablePath) as caught:
            await fetch_file(client, REPO, path, BASIS)

        assert "repository root" in str(caught.value)
        assert not wire.called

    async def test_a_branch_with_a_slash_is_refused_with_advice(self, client, wire):
        with pytest.raises(UnreadablePath) as caught:
            await fetch_file(client, REPO, FILE, "feature/retry")

        assert "commit hash" in str(caught.value)
        assert not wire.called

    async def test_an_empty_ref_is_refused(self, client, wire):
        with pytest.raises(UnreadablePath):
            await fetch_file(client, REPO, FILE, "   ")

        assert not wire.called

    async def test_an_unlisted_repository_never_reaches_the_network(self, client, wire):
        with pytest.raises(Forbidden):
            await fetch_file(client, UNLISTED, FILE, BASIS)

        assert not wire.called


class TestListingADirectory:
    async def test_it_lists_the_entries(self, client, wire):
        wire.will_return(json_response(fixtures.directory()))

        found = await fetch_directory(client, REPO, "src/app", BASIS, limit=200)

        assert [entry.path for entry in found.entries] == [
            "src/app/handlers",
            "src/app/__init__.py",
            "src/app/retry.py",
        ]

    async def test_directories_sort_before_files(self, client, wire):
        wire.will_return(json_response(fixtures.directory()))

        found = await fetch_directory(client, REPO, "src/app", BASIS, limit=200)

        assert found.entries[0].is_directory
        assert found.entries[0].name.endswith("/")

    async def test_the_root_is_readable(self, client, wire):
        wire.will_return(json_response(fixtures.directory()))

        await fetch_directory(client, REPO, "", BASIS, limit=200)

        assert wire.last.url.path == f"/2.0/repositories/streamstech/db-explorer/src/{BASIS}/"

    async def test_a_long_listing_says_it_was_truncated(self, client, wire):
        wire.will_return(json_response(fixtures.directory()))

        found = await fetch_directory(client, REPO, "src/app", BASIS, limit=1)

        assert found.truncated
        assert "1 of 3" in found.to_markdown()

    async def test_the_listing_is_fenced_as_untrusted(self, client, wire):
        wire.will_return(json_response(fixtures.directory()))

        rendered = (await fetch_directory(client, REPO, "src/app", BASIS, limit=200)).to_markdown()

        assert UNTRUSTED_OPEN in rendered


class TestCommitHistory:
    async def test_a_ref_reads_the_repository_history(self, client, wire):
        wire.will_return(json_response(fixtures.commits()))

        found = await fetch_commits(client, repository=REPO, ref="main", limit=50)

        assert wire.last.url.path == "/2.0/repositories/streamstech/db-explorer/commits/main"
        assert found.commits[0].subject == "Retry the upstream call"

    async def test_a_pull_request_reads_its_own_commits(self, client, wire):
        wire.will_return(json_response(fixtures.commits()))

        await fetch_commits(
            client, pull_request=PullRequestRef("streamstech", "db-explorer", 42), limit=50
        )

        assert wire.last.url.path == (
            "/2.0/repositories/streamstech/db-explorer/pullrequests/42/commits"
        )

    async def test_both_at_once_is_refused_as_two_different_questions(self, client, wire):
        with pytest.raises(AmbiguousRequest) as caught:
            await fetch_commits(
                client,
                repository=REPO,
                ref="main",
                pull_request=PullRequestRef("streamstech", "db-explorer", 42),
            )

        assert "not both" in str(caught.value)
        assert not wire.called

    async def test_neither_is_refused_with_what_each_answers(self, client, wire):
        with pytest.raises(AmbiguousRequest) as caught:
            await fetch_commits(client)

        assert "ref" in str(caught.value) and "pull request" in str(caught.value)
        assert not wire.called

    async def test_a_ref_without_a_repository_says_so(self, client, wire):
        with pytest.raises(AmbiguousRequest) as caught:
            await fetch_commits(client, ref="main")

        assert "workspace/repo" in str(caught.value)
        assert not wire.called

    async def test_an_author_with_no_bitbucket_account_falls_back_to_the_raw_name(
        self, client, wire
    ):
        wire.will_return(json_response(fixtures.commits()))

        found = await fetch_commits(client, repository=REPO, ref="main", limit=50)

        assert found.commits[1].author == "someone@example.com"

    async def test_a_capped_history_says_it_is_the_top_of_the_list(self, client, wire):
        wire.will_return(json_response(fixtures.commits()))

        found = await fetch_commits(client, repository=REPO, ref="main", limit=1)

        assert found.truncated
        assert "not all of it" in found.to_markdown()

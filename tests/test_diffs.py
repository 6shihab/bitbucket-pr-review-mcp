"""Parsing the diff, slicing it by path, and remembering it against the Review Basis.

The cache tests are the ones with teeth. A diff kept against the pull request rather
than the Basis would still be served after a force-push — a diff of code that no longer
exists, which reviews cleanly and anchors comments to nothing.
"""

from __future__ import annotations

import httpx
import pytest

from bitbucket_pr_review_mcp.diffs import (
    DiffCache,
    PathNotInDiff,
    diff_markdown,
    fetch_diff,
    parse_diff,
    select,
)
from bitbucket_pr_review_mcp.guard import Forbidden
from bitbucket_pr_review_mcp.references import PullRequestRef
from bitbucket_pr_review_mcp.render import UNTRUSTED_CLOSE, UNTRUSTED_OPEN

from . import fixtures

REF = PullRequestRef("streamstech", "db-explorer", 42)
BASIS = fixtures.BASIS
OTHER_BASIS = fixtures.OLD_BASIS


@pytest.fixture
def diff():
    return parse_diff(fixtures.UNIFIED_DIFF, BASIS)


class TestParsing:
    def test_it_finds_every_changed_file(self, diff):
        assert diff.paths() == (
            "src/app/retry.py",
            "tests/test_retry.py",
            "assets/logo.png",
            "uv.lock",
            "docs/notes.md",
        )

    def test_a_file_with_two_hunks_keeps_both(self, diff):
        assert len(diff.for_path("src/app/retry.py").hunks) == 2

    def test_hunk_ranges_are_read_from_the_header(self, diff):
        first = diff.for_path("src/app/retry.py").hunks[0]

        assert (first.old_start, first.old_count) == (12, 7)
        assert (first.new_start, first.new_count) == (12, 11)

    def test_a_hunk_keeps_its_lines_verbatim(self, diff):
        first = diff.for_path("src/app/retry.py").hunks[0]

        assert "-    return session.post(UPSTREAM, json=payload)" in first.lines
        assert "+    raise UpstreamUnavailable(UPSTREAM)" in first.lines

    def test_a_new_file_has_no_old_path(self, diff):
        added = diff.for_path("tests/test_retry.py")

        assert added.old_path is None
        assert added.new_path == "tests/test_retry.py"

    def test_a_rename_keeps_both_names(self, diff):
        renamed = diff.for_path("docs/notes.md")

        assert renamed.is_rename
        assert renamed.old_path == "docs/old-notes.md"

    def test_a_binary_file_is_recognised_and_has_no_hunks(self, diff):
        binary = diff.for_path("assets/logo.png")

        assert binary.is_binary
        assert binary.hunks == ()

    def test_an_empty_diff_parses_to_nothing(self):
        assert parse_diff("", BASIS).files == ()

    def test_round_tripping_a_file_keeps_its_header_and_hunks(self, diff):
        text = diff.for_path("uv.lock").to_text()

        assert text.startswith("diff --git a/uv.lock b/uv.lock")
        assert '+version = "0.28.1"' in text


class TestSelectingAFile:
    def test_an_exact_path_wins(self, diff):
        assert select(diff, "src/app/retry.py").path == "src/app/retry.py"

    def test_a_deleted_file_is_found_by_its_old_name(self, diff):
        assert select(diff, "docs/old-notes.md").path == "docs/notes.md"

    def test_a_unique_file_name_is_enough(self, diff):
        assert select(diff, "retry.py").path == "src/app/retry.py"

    def test_an_unchanged_path_names_the_tool_that_lists_the_real_ones(self, diff):
        with pytest.raises(PathNotInDiff) as caught:
            select(diff, "src/app/untouched.py")

        assert "bitbucket_get_pull_request_changes" in str(caught.value)

    def test_a_trailing_part_of_the_path_is_enough(self, diff):
        assert select(diff, "app/retry.py").path == "src/app/retry.py"

    def test_a_near_miss_suggests_the_path_it_probably_meant(self, diff):
        with pytest.raises(PathNotInDiff) as caught:
            select(diff, "lib/retry.py")

        assert "src/app/retry.py" in str(caught.value)


class TestTheCache:
    async def test_a_diff_read_twice_is_fetched_once(self, client, wire):
        wire.will_return(
            httpx.Response(200, text=fixtures.UNIFIED_DIFF),
            httpx.Response(200, text="diff --git a/other b/other\n"),
        )
        cache = DiffCache()

        first = await fetch_diff(client, REF, BASIS, cache)
        second = await fetch_diff(client, REF, BASIS, cache)

        assert first is second
        assert len(wire.requests) == 1

    async def test_the_basis_moving_misses_the_cache(self, client, wire):
        wire.will_return(
            httpx.Response(200, text=fixtures.UNIFIED_DIFF),
            httpx.Response(200, text="diff --git a/rewritten b/rewritten\n"),
        )
        cache = DiffCache()

        await fetch_diff(client, REF, BASIS, cache)
        after_force_push = await fetch_diff(client, REF, OTHER_BASIS, cache)

        assert after_force_push.paths() == ("rewritten",)
        assert len(wire.requests) == 2, "a moved Basis must not be served from the cache"

    def test_it_does_not_grow_without_bound(self, diff):
        cache = DiffCache(capacity=2)

        for index in range(5):
            cache.put(PullRequestRef("streamstech", "db-explorer", index), diff)

        assert len(cache) == 2

    def test_a_different_pull_request_is_a_different_entry(self, diff):
        cache = DiffCache()
        cache.put(REF, diff)

        assert cache.get(PullRequestRef("streamstech", "db-explorer", 7), BASIS) is None


class TestFetching:
    async def test_it_asks_for_the_pull_request_diff(self, client, wire):
        wire.will_return(httpx.Response(200, text=fixtures.UNIFIED_DIFF))

        await fetch_diff(client, REF, BASIS)

        assert wire.last.url.path == (
            "/2.0/repositories/streamstech/db-explorer/pullrequests/42/diff"
        )

    async def test_it_follows_bitbuckets_redirect_to_the_real_diff(self, client, wire):
        redirect_to = (
            "https://api.bitbucket.org/2.0/repositories/streamstech/db-explorer/diff/"
            "streamstech/db-explorer:9f2c4a1b7e5d%0D0a1b2c3d4e5f"
        )
        wire.will_return(
            httpx.Response(302, headers={"location": redirect_to}),
            httpx.Response(200, text=fixtures.UNIFIED_DIFF),
        )

        fetched = await fetch_diff(client, REF, BASIS)

        assert len(fetched.files) == 5
        assert len(wire.requests) == 2

    async def test_a_redirect_off_bitbucket_is_refused_by_the_guard(self, client, wire):
        wire.will_return(httpx.Response(302, headers={"location": "https://evil.test/diff"}))

        with pytest.raises(Forbidden):
            await fetch_diff(client, REF, BASIS)


class TestRendering:
    def test_the_whole_diff_is_fenced_as_untrusted(self, diff):
        rendered = diff_markdown(REF, diff, None, limit=100_000)

        assert UNTRUSTED_OPEN in rendered and UNTRUSTED_CLOSE in rendered
        assert "src/app/retry.py" in rendered

    def test_one_file_carries_only_that_file(self, diff):
        rendered = diff_markdown(REF, diff, diff.for_path("uv.lock"), limit=100_000)

        assert "uv.lock" in rendered
        assert "src/app/retry.py" not in rendered

    def test_it_names_the_review_basis(self, diff):
        assert BASIS in diff_markdown(REF, diff, None, limit=100_000)

    def test_truncation_is_stated_and_suggests_narrowing(self, diff):
        rendered = diff_markdown(REF, diff, None, limit=200)

        assert "Truncated" in rendered
        assert "path=" in rendered, "the advice must be to narrow, not to raise a limit"

    def test_the_truncation_notice_sits_outside_the_fence(self, diff):
        rendered = diff_markdown(REF, diff, None, limit=200)

        assert rendered.index("Truncated") < rendered.index(UNTRUSTED_OPEN)

    def test_a_binary_file_says_there_is_nothing_to_anchor_to(self, diff):
        rendered = diff_markdown(REF, diff, diff.for_path("assets/logo.png"), limit=100_000)

        assert "binary" in rendered.lower()

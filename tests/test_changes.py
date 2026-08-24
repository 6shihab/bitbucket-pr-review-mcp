"""The manifest, and the flags that keep a review off the checksums."""

from __future__ import annotations

import httpx
import pytest

from bitbucket_pr_review_mcp.changes import (
    BINARY,
    GENERATED,
    LOCKFILE,
    changes_path,
    classify,
    fetch_changes,
)
from bitbucket_pr_review_mcp.references import PullRequestRef
from bitbucket_pr_review_mcp.render import UNTRUSTED_OPEN

from . import fixtures

REF = PullRequestRef("streamstech", "db-explorer", 42)
BASIS = fixtures.BASIS


@pytest.fixture
async def changes(client, wire):
    wire.will_return(httpx.Response(200, json=fixtures.diffstat()))
    return await fetch_changes(client, REF, BASIS, limit=300)


class TestTheManifest:
    async def test_it_asks_the_diffstat_endpoint(self, client, wire):
        wire.will_return(httpx.Response(200, json=fixtures.diffstat()))

        await fetch_changes(client, REF, BASIS, limit=300)

        assert wire.last.url.path == changes_path(REF)

    async def test_every_changed_file_is_listed(self, changes):
        assert [file.path for file in changes.files] == [
            "src/app/retry.py",
            "tests/test_retry.py",
            "assets/logo.png",
            "uv.lock",
            "docs/notes.md",
            "src/app/legacy.py",
        ]

    async def test_each_row_carries_its_change_type_and_line_counts(self, changes):
        first = changes.files[0]

        assert (first.status, first.added, first.removed) == ("modified", 6, 2)

    async def test_a_deleted_file_is_named_by_the_path_it_had(self, changes):
        removed = changes.files[-1]

        assert removed.path == "src/app/legacy.py"
        assert removed.status == "removed"

    async def test_a_rename_keeps_the_name_it_had(self, changes):
        renamed = next(file for file in changes.files if file.path == "docs/notes.md")

        assert renamed.is_rename
        assert renamed.old_path == "docs/old-notes.md"

    async def test_it_walks_past_the_first_page(self, client, wire):
        wire.will_return(
            httpx.Response(
                200,
                json=fixtures.diffstat(
                    next_page="https://api.bitbucket.org/2.0/repositories/streamstech/"
                    "db-explorer/pullrequests/42/diffstat?page=2"
                ),
            ),
            httpx.Response(200, json={"values": []}),
        )

        found = await fetch_changes(client, REF, BASIS, limit=300)

        assert len(wire.requests) == 2
        assert found.more_remain is False
        assert found.total == 6


class TestTheFlags:
    async def test_a_binary_file_is_flagged(self, changes):
        binary = next(file for file in changes.files if file.path == "assets/logo.png")

        assert BINARY in binary.flags

    async def test_a_lockfile_is_flagged(self, changes):
        lock = next(file for file in changes.files if file.path == "uv.lock")

        assert LOCKFILE in lock.flags

    async def test_ordinary_source_is_not_flagged(self, changes):
        source = next(file for file in changes.files if file.path == "src/app/retry.py")

        assert source.flags == ()
        assert source.worth_reading

    @pytest.mark.parametrize(
        "path,flag",
        [
            ("web/static/app.min.js", GENERATED),
            ("api/schema_pb2.py", GENERATED),
            ("proto/user.pb.go", GENERATED),
            ("src/generated/client.ts", GENERATED),
            ("dist/bundle.js", GENERATED),
            ("package-lock.json", LOCKFILE),
            ("Cargo.lock", LOCKFILE),
            ("go.sum", LOCKFILE),
            ("docs/diagram.png", BINARY),
            ("fonts/Inter.woff2", BINARY),
        ],
    )
    def test_it_recognises_the_usual_suspects(self, path, flag):
        assert flag in classify(path)

    @pytest.mark.parametrize(
        "path", ["src/app/retry.py", "README.md", "config/settings.yaml", "lockfiles.md"]
    )
    def test_it_leaves_ordinary_files_alone(self, path):
        assert classify(path) == ()

    def test_a_generated_directory_deeper_in_the_tree_still_counts(self):
        assert GENERATED in classify("services/api/__generated__/types.ts")


class TestRendering:
    async def test_the_table_is_fenced_as_untrusted(self, changes):
        rendered = changes.to_markdown()

        assert UNTRUSTED_OPEN in rendered
        assert "`src/app/retry.py`" in rendered

    async def test_it_names_the_review_basis(self, changes):
        assert BASIS in changes.to_markdown()

    async def test_it_points_at_the_diff_tool(self, changes):
        assert "bitbucket_get_pull_request_diff" in changes.to_markdown()

    async def test_a_short_manifest_says_nothing_about_truncation(self, changes):
        assert not changes.truncated
        assert "Truncated" not in changes.to_markdown()

    async def test_a_capped_manifest_says_how_many_it_left_out(self, client, wire):
        wire.will_return(httpx.Response(200, json=fixtures.diffstat()))

        capped = await fetch_changes(client, REF, BASIS, limit=2)

        assert capped.truncated
        assert "2 of 6" in capped.to_markdown()
        assert "do not assume the unlisted files are unchanged" in capped.to_markdown().lower()

"""The readers, run against responses recorded from Bitbucket Cloud.

Ticket 01 left one criterion unmet: its fixtures were modelled on documented shapes
rather than captured, and hand-written fixtures agree with our misunderstandings.
These three files are the real thing — `jantrik/admin-client` pull request 2476, a test
pull request kept open for exactly this, read on 2026-08-24 with a real credential.

The pull request is deliberately trivial (one line of one README), so these tests do not
replace the richer synthetic fixtures in `fixtures.py`; they check the shapes those
fixtures assume. Two assumptions were wrong, and both are asserted here so they stay
fixed: `/diffstat` redirects like `/diff` does, and `source.commit.hash` comes back
abbreviated to twelve characters while the same commit is spelled in full elsewhere in
the same payload.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from bitbucket_pr_review_mcp.changes import read_changed_file
from bitbucket_pr_review_mcp.diffs import parse_diff
from bitbucket_pr_review_mcp.pullrequests import read_pull_request
from bitbucket_pr_review_mcp.references import PullRequestRef

RECORDED = Path(__file__).parent / "recorded"
REF = PullRequestRef("jantrik", "admin-client", 2476)


def recorded(name: str):
    body = (RECORDED / name).read_text(encoding="utf-8")
    return json.loads(body) if name.endswith(".json") else body


class TestARecordedPullRequest:
    @pytest.fixture
    def payload(self):
        return recorded("pull_request.json")

    def test_every_field_we_read_is_present(self, payload):
        found = read_pull_request(REF, payload)

        assert found.title == "this is test commit (Do not Merge or Delete)"
        assert found.state == "OPEN"
        assert found.author == "Md Shihabul Hasan Shihab"
        assert found.source_branch == "test-src"
        assert found.destination_branch == "test-dest"
        assert found.html_url.endswith("/pull-requests/2476")

    def test_the_review_basis_comes_back_abbreviated(self, payload):
        """Twelve characters, not forty — and the same commit appears in full elsewhere.

        Ticket 07 re-checks the Basis against the pull request's current head before any
        write. Comparing these two spellings with `==` would refuse every batch, so that
        comparison has to be prefix-aware. Asserting it here means the day Bitbucket
        starts sending forty, this test says so.
        """
        found = read_pull_request(REF, payload)

        assert len(found.review_basis) == 12
        assert payload["source"]["commit"]["links"]["self"]["href"].endswith(found.review_basis)

    def test_an_empty_description_is_read_as_empty_not_missing(self, payload):
        assert read_pull_request(REF, payload).description == ""


class TestARecordedDiffstat:
    def test_the_manifest_reads_the_recorded_entry(self):
        entries = recorded("diffstat.json")["values"]

        files = [read_changed_file(entry) for entry in entries]

        assert [file.path for file in files] == ["README.md"]
        assert files[0].status == "modified"
        assert (files[0].added, files[0].removed) == (1, 1)
        assert files[0].flags == ()

    def test_the_entry_carries_no_binary_flag_of_its_own(self):
        """Which is why `classify` works from the path: there is nothing else to work from."""
        entry = recorded("diffstat.json")["values"][0]

        assert not any("binary" in key for key in entry)


class TestARecordedDiff:
    def test_it_parses_into_one_file_with_one_hunk(self):
        diff = parse_diff(recorded("diff.patch"), "f90a2239dbc1")

        assert diff.paths() == ("README.md",)
        assert len(diff.files[0].hunks) == 1

    def test_the_hunk_range_matches_the_header(self):
        hunk = parse_diff(recorded("diff.patch"), "f90a2239dbc1").files[0].hunks[0]

        assert (hunk.old_start, hunk.old_count) == (1, 5)
        assert (hunk.new_start, hunk.new_count) == (1, 5)
        assert "+# New Line" in hunk.lines

    def test_context_lines_keep_their_leading_space(self):
        hunk = parse_diff(recorded("diff.patch"), "f90a2239dbc1").files[0].hunks[0]

        assert hunk.lines[0].startswith(" #")

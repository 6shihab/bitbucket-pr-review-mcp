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
from bitbucket_pr_review_mcp.comments import read_comment
from bitbucket_pr_review_mcp.commits import read_commit
from bitbucket_pr_review_mcp.diffs import parse_diff
from bitbucket_pr_review_mcp.pullrequests import read_pull_request
from bitbucket_pr_review_mcp.references import PullRequestRef, Repository
from bitbucket_pr_review_mcp.repositories import read_repository
from bitbucket_pr_review_mcp.search import repository_of
from bitbucket_pr_review_mcp.source import read_entry
from bitbucket_pr_review_mcp.summary import MARKER, canonical, tally_of

RECORDED = Path(__file__).parent / "recorded"
REF = PullRequestRef("jantrik", "admin-client", 2476)

# The account the recorded comment was posted by — read from the recording, not assumed.
OUR_REAL_ACCOUNT = '712020:0132e6b0-9ae2-4292-8d60-dee036f20150'


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


class TestRecordedRepositoryReads:
    """Ticket 04's shapes, recorded on 2026-08-24 from the same repository.

    `search.json` has its `segments[].text` replaced with REDACTED — every key and every
    nesting level is exactly as Bitbucket sent it, but the matched lines were somebody's
    private source and this repository is not where they belong. The structure is what
    these tests are for; the text is not.
    """

    def test_repository_metadata_carries_the_default_branch(self):
        found = read_repository(Repository("jantrik", "admin-client"), recorded("repository.json"))

        assert found.default_branch == "dev", "not every repository calls it main"
        assert found.is_private
        assert found.language == "typescript"

    def test_a_directory_listing_distinguishes_files_from_directories(self):
        entries = [read_entry(value) for value in recorded("directory.json")["values"]]

        assert any(entry.is_directory for entry in entries)
        assert any(not entry.is_directory and entry.size for entry in entries)

    def test_a_commit_reads_its_author_and_subject(self):
        commit = read_commit(recorded("commits.json")["values"][0])

        assert len(commit.hash) == 40, "commits/ spells hashes in full, unlike the pull request"
        assert commit.author and commit.subject

    def test_a_search_result_states_its_origin_only_in_the_file_link(self):
        """The field the allowlist backstop reads. There is no repository field at all."""
        value = recorded("search.json")["values"][0]

        assert "repository" not in (value.get("file") or {}).get("commit", {})
        assert "/2.0/repositories/jantrik/admin-client/src/" in value["file"]["links"]["self"][
            "href"
        ]

    def test_the_origin_reader_finds_that_repository(self):
        value = recorded("search.json")["values"][0]

        assert repository_of(value) == Repository("jantrik", "admin-client")


def comment(comment_id: int) -> dict:
    """One recorded comment by id. Positions shift as the recording grows; ids do not."""
    found = {value["id"]: value for value in recorded("comments.json")["values"]}
    return found[comment_id]


class TestARecordedComment:
    """The first comment this server ever posted, read back from Bitbucket.

    It is the reason ranges work. The reference this was built from describes `inline`
    as `{path, from, to}`; the real object has `start_from` and `start_to` as well, so
    Bitbucket can anchor to a block and the first implementation here was wrong to say
    it could not. It has since been deleted by hand, which is why its body is empty and
    why it is worth keeping in the recording.
    """

    def test_the_inline_object_carries_the_range_fields(self):
        inline = comment(847207531)["inline"]

        assert set(inline) == {"path", "from", "to", "start_from", "start_to"}

    def test_a_single_line_anchor_leaves_the_range_fields_null(self):
        inline = comment(847218781)["inline"]

        assert inline["to"] == 2 and inline["from"] is None
        assert inline["start_to"] is None and inline["start_from"] is None

    def test_there_is_no_outdated_field_on_a_live_anchor(self):
        """Which is why a null pair is also read as orphaned: absence is the only signal."""
        assert "outdated" not in comment(847218781)["inline"]

    def test_the_reader_places_it_where_bitbucket_says(self):
        read = read_comment(comment(847218781), OUR_REAL_ACCOUNT)

        assert read.anchor == "README.md:2 (added/context)"
        assert read.is_ours
        assert not read.is_orphaned

    def test_the_attribution_footer_survived_the_round_trip(self):
        read = read_comment(comment(847218781), OUR_REAL_ACCOUNT)

        assert "Machine-generated review comment" in read.body
        assert "bitbucket-pr-review-mcp" in read.body


class TestARecordedRangeComment:
    """Which end of the range each field names, settled by posting one.

    Sent `start_to: 3, to: 5`; Bitbucket stored exactly that. So `start_to` is the first
    line of the block and `to` is the last — the badge lands on the last line, and the
    read-back reports the whole range.
    """

    def test_bitbucket_kept_the_range_it_was_given(self):
        inline = comment(847218798)["inline"]

        assert inline["start_to"] == 3, "the first line of the block"
        assert inline["to"] == 5, "the last line of the block"
        assert inline["start_from"] is None and inline["from"] is None

    def test_the_reader_reports_the_whole_block(self):
        read = read_comment(comment(847218798), OUR_REAL_ACCOUNT)

        assert read.anchor == "README.md:3-5 (added/context)"
        assert read.start_line == 3
        assert read.line == 5

    def test_the_range_is_named_in_the_body_as_well(self):
        """A reader who only sees the badge on the last line still learns the block."""
        read = read_comment(comment(847218798), OUR_REAL_ACCOUNT)

        assert "context lines 3–5" in read.body


class TestARecordedRemovedSideComment:
    """The inversion trap, proved live.

    A comment on a removed line came back as `{"from": 2, "to": null}` — the old file's
    numbering, with nothing on the new side. Every unit test asserts this mapping; this
    is the one that watched Bitbucket do it.
    """

    def test_a_removed_line_anchors_on_the_old_side_only(self):
        inline = comment(847218787)["inline"]

        assert inline["from"] == 2
        assert inline["to"] is None

    def test_the_reader_calls_it_removed(self):
        read = read_comment(comment(847218787), OUR_REAL_ACCOUNT)

        assert read.anchor == "README.md:2 (removed)"
        assert read.old_line == 2 and read.new_line is None

    def test_it_sits_at_the_same_number_as_a_different_added_line(self):
        """`added 2` and `removed 2` are two different places, and both were posted."""
        added = read_comment(comment(847218781), OUR_REAL_ACCOUNT)
        removed = read_comment(comment(847218787), OUR_REAL_ACCOUNT)

        assert added.line == removed.line == 2
        assert added.anchor != removed.anchor


class TestARecordedDeletedComment:
    """What deletion looks like from the API: the id survives, the body does not."""

    def test_a_deleted_comment_keeps_its_id_and_loses_its_body(self):
        deleted = comment(847207531)

        assert deleted["deleted"] is True
        assert deleted["content"]["raw"] == ""

    def test_the_reader_marks_it_rather_than_hiding_it(self):
        read = read_comment(comment(847207531), OUR_REAL_ACCOUNT)

        assert read.is_deleted
        assert "deleted" in read.flags()


class TestARecordedSummaryComment:
    """The canonical summary, posted and then updated in place on a live pull request.

    The first version of it carried the marker in an HTML comment. Bitbucket's rendered
    html came back as `<p>&lt;!-- bitbucket-pr-review-mcp:summary/1 --&gt;</p>` — escaped
    and visible, not dropped — so the marker moved into the Attribution Footer, where it
    reads as a tool identifier instead of stray markup. This is that comment after the
    move, which found itself by the token that both formats share.
    """

    SUMMARY = 847224255

    def test_it_is_recognised_as_the_canonical_summary(self):
        ours = tuple(
            read_comment(value, OUR_REAL_ACCOUNT)
            for value in recorded("comments.json")["values"]
            if not value.get("deleted")
        )

        assert canonical(ours) is not None
        assert canonical(ours).id == self.SUMMARY

    def test_there_is_exactly_one_of_them(self):
        summaries = [
            value
            for value in recorded("comments.json")["values"]
            if not value.get("deleted") and MARKER in value["content"]["raw"]
        ]

        assert len(summaries) == 1, "a second review updates, it does not stack"

    def test_it_is_not_anchored_to_a_line(self):
        assert comment(self.SUMMARY).get("inline") is None

    def test_the_rendered_comment_opens_with_its_heading_not_with_markup(self):
        rendered = comment(self.SUMMARY)["content"]["html"]

        assert rendered.startswith("<h1")
        assert "&lt;!--" not in rendered, "the reason the marker is not an HTML comment"

    def test_the_marker_survives_in_the_footer(self):
        raw = comment(self.SUMMARY)["content"]["raw"]

        assert MARKER in raw
        assert raw.index(MARKER) > raw.index("---")

    def test_the_tally_it_carries_matches_the_findings_on_the_pull_request(self):
        values = recorded("comments.json")["values"]
        ours = tuple(read_comment(value, OUR_REAL_ACCOUNT) for value in values)
        live_ours = tuple(one for one in ours if not one.is_deleted)

        counted = tally_of(live_ours)

        assert counted["MEDIUM"] == 1 and counted["LOW"] == 2
        assert "| MEDIUM (info) | 1 |" in comment(self.SUMMARY)["content"]["raw"]

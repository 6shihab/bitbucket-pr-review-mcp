"""Where a comment lands, and the translation that is easy to get backwards.

`inline.to` is the new file and `inline.from` is the old one. Inverting them does not
raise: it attaches a comment to a different line of real code, and somebody acts on it.
So these tests assert the mapping in both directions and on every side of the diff.

The fixture diff's first hunk is `@@ -12,7 +12,11 @@`:

    12  12   session = build_session()
    13   -   -    return session.post(UPSTREAM, json=payload)
     -  13   +    for attempt in range(RETRIES):
     -  14   +        try:
     -  15   +            return session.post(UPSTREAM, json=payload)
     -  16   +        except TimeoutError:
     -  17   +            sleep(backoff(attempt))
     -  18   +    raise UpstreamUnavailable(UPSTREAM)
    14  19
    15  20
    16  21   def build_session():
"""

from __future__ import annotations

import pytest

from bitbucket_pr_review_mcp.anchors import (
    ADDED,
    CONTEXT,
    REMOVED,
    Anchor,
    AnchorNotInDiff,
    anchor_in,
    anchorable,
)
from bitbucket_pr_review_mcp.diffs import PathNotInDiff, parse_diff

from . import fixtures

FILE = "src/app/retry.py"


@pytest.fixture
def diff():
    return parse_diff(fixtures.UNIFIED_DIFF, fixtures.BASIS)


def anchor(line: int, side: str, through: int | None = None, path: str = FILE) -> Anchor:
    return Anchor(path=path, line=line, side=side, through=through)


class TestNumberingTheHunk:
    def test_added_lines_are_numbered_on_the_new_side_only(self, diff):
        lines = [line for line in anchorable(diff.for_path(FILE)) if line.side == ADDED]

        assert [line.number for line in lines][:3] == [13, 14, 15]
        assert all(line.old_line is None for line in lines)

    def test_removed_lines_are_numbered_on_the_old_side_only(self, diff):
        lines = [line for line in anchorable(diff.for_path(FILE)) if line.side == REMOVED]

        assert lines[0].number == 13
        assert all(line.new_line is None for line in lines)

    def test_context_lines_carry_both_numbers(self, diff):
        first = next(line for line in anchorable(diff.for_path(FILE)) if line.side == CONTEXT)

        assert (first.old_line, first.new_line) == (12, 12)
        assert first.number == first.new_line, "context anchors by the new side"

    def test_the_two_counters_advance_independently(self, diff):
        lines = anchorable(diff.for_path(FILE))
        context_after_the_change = [
            line for line in lines if line.side == CONTEXT and line.old_line == 14
        ]

        assert context_after_the_change[0].new_line == 19, (
            "six added lines and one removed shift the new side by five"
        )


class TestTranslatingToBitbucket:
    def test_an_added_line_becomes_a_new_side_anchor(self, diff):
        inline = anchor_in(diff, anchor(14, ADDED)).inline()

        assert inline == {"path": FILE, "to": 14}
        assert "from" not in inline, "an added line has no old-file line"

    def test_a_removed_line_becomes_an_old_side_anchor(self, diff):
        inline = anchor_in(diff, anchor(13, REMOVED)).inline()

        assert inline == {"path": FILE, "from": 13}
        assert "to" not in inline, "a removed line has no new-file line"

    def test_a_context_line_anchors_on_the_new_side(self, diff):
        inline = anchor_in(diff, anchor(12, CONTEXT)).inline()

        assert inline == {"path": FILE, "to": 12}

    def test_the_same_number_on_two_sides_is_two_different_places(self, diff):
        added = anchor_in(diff, anchor(13, ADDED)).inline()
        removed = anchor_in(diff, anchor(13, REMOVED)).inline()

        assert added == {"path": FILE, "to": 13}
        assert removed == {"path": FILE, "from": 13}
        assert added != removed, "the side is what makes 13 mean one line or another"


class TestRanges:
    def test_a_range_anchors_at_its_first_line(self, diff):
        anchored = anchor_in(diff, anchor(13, ADDED, through=18))

        assert anchored.inline() == {"path": FILE, "to": 13}
        assert anchored.anchor.is_range

    def test_every_line_of_the_range_has_to_exist(self, diff):
        with pytest.raises(AnchorNotInDiff) as caught:
            anchor_in(diff, anchor(13, ADDED, through=99))

        assert "19" in str(caught.value) or "no added line" in str(caught.value)

    def test_a_backwards_range_is_refused_at_construction(self):
        with pytest.raises(AnchorNotInDiff) as caught:
            anchor(18, ADDED, through=13)

        assert "ends before it starts" in str(caught.value)

    def test_a_single_line_range_is_not_a_range(self, diff):
        anchored = anchor_in(diff, anchor(14, ADDED, through=14))

        assert not anchored.anchor.is_range


class TestRefusingAnInventedLine:
    def test_a_line_not_in_any_hunk_is_refused(self, diff):
        with pytest.raises(AnchorNotInDiff) as caught:
            anchor_in(diff, anchor(500, ADDED))

        assert "no added line 500" in str(caught.value)

    def test_the_refusal_names_the_nearest_lines_that_would_work(self, diff):
        with pytest.raises(AnchorNotInDiff) as caught:
            anchor_in(diff, anchor(30, ADDED))

        message = str(caught.value)
        assert "added line 18" in message
        assert "raise UpstreamUnavailable" in message, "the text, so it can tell which is which"

    def test_the_refusal_says_what_a_wrong_side_would_cost(self, diff):
        with pytest.raises(AnchorNotInDiff) as caught:
            anchor_in(diff, anchor(19, REMOVED))

        assert "attach the comment to code nobody is reviewing" in str(caught.value)

    def test_a_file_with_no_lines_on_that_side_says_which_sides_it_has(self, diff):
        with pytest.raises(AnchorNotInDiff) as caught:
            anchor_in(diff, anchor(1, REMOVED, path="tests/test_retry.py"))

        assert "no removed lines" in str(caught.value)
        assert "added" in str(caught.value)

    def test_a_path_not_in_the_diff_is_refused_by_the_diff_itself(self, diff):
        with pytest.raises(PathNotInDiff):
            anchor_in(diff, anchor(1, ADDED, path="src/app/imagined.py"))

    @pytest.mark.parametrize("side", ["new", "old", "left", "", "ADDED?"])
    def test_a_side_that_is_not_a_side_is_refused(self, side):
        with pytest.raises(AnchorNotInDiff) as caught:
            anchor(14, side)

        assert "added" in str(caught.value) and "removed" in str(caught.value)

    @pytest.mark.parametrize("line", [0, -1])
    def test_a_line_number_that_is_not_one_is_refused(self, line):
        with pytest.raises(AnchorNotInDiff):
            anchor(line, ADDED)


class TestABinaryFile:
    def test_it_has_nothing_to_anchor_to(self, diff):
        with pytest.raises(AnchorNotInDiff) as caught:
            anchor_in(diff, anchor(1, ADDED, path="assets/logo.png"))

        assert "no added lines" in str(caught.value)

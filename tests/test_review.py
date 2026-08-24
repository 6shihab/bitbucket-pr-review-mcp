"""Posting a whole review, and behaving on the third pass over the same code.

The property worth defending is where a partial write can come from. Validation is a
phase: every Anchor and the Review Basis are checked before the first POST, so a
half-posted review can only be the network's doing. And when that happens, nothing is
deleted — the report says what landed, because a comment a colleague has already replied
to is worth more than a tidy list.
"""

from __future__ import annotations

import httpx
import pytest

from bitbucket_pr_review_mcp.anchors import ADDED, CONTEXT, REMOVED, Anchor
from bitbucket_pr_review_mcp.findings import Finding, Reviewer
from bitbucket_pr_review_mcp.posting import BasisMoved
from bitbucket_pr_review_mcp.references import PullRequestRef
from bitbucket_pr_review_mcp.review import (
    DUPLICATE,
    FAILED,
    NOT_ATTEMPTED,
    ORPHANED,
    POSTED,
    BatchRefused,
    post_review,
)

from . import fixtures

REF = PullRequestRef("streamstech", "db-explorer", 42)
BASIS = fixtures.BASIS
FILE = "src/app/retry.py"
REVIEWER = Reviewer(display_name="Anwar Hossain", email="reviewer@streamstech.com")
OURS = fixtures.OUR_ACCOUNT_ID


def finding(message="`RETRIES` is undefined here.", line=14, side=ADDED, through=None,
            severity="HIGH") -> Finding:
    return Finding.of(
        severity=severity,
        category="correctness",
        message=message,
        anchor=Anchor(path=FILE, line=line, side=side, through=through),
    )


def created(comment_id: int, line: int = 14, orphaned: bool = False) -> httpx.Response:
    inline = (
        {"path": FILE, "from": None, "to": None}
        if orphaned
        else {"path": FILE, "from": None, "to": line, "start_from": None, "start_to": None}
    )
    return httpx.Response(
        201,
        json={
            "id": comment_id,
            "content": {"raw": "posted"},
            "user": {"account_id": OURS, "display_name": "Anwar Hossain"},
            "created_on": "2026-08-24T12:00:00.000000+00:00",
            "inline": inline,
        },
    )


def reads(*, basis=BASIS, existing=None):
    """The three reads every batch makes before it writes anything."""
    return [
        httpx.Response(200, json=fixtures.pull_request(basis=basis)),
        httpx.Response(200, text=fixtures.UNIFIED_DIFF),
        httpx.Response(200, json=existing if existing is not None else {"values": []}),
    ]


async def post(client, findings, basis=BASIS, our_account=OURS):
    return await post_review(client, REF, findings, basis, REVIEWER, our_account)


class TestPostingMany:
    async def test_each_finding_lands_on_its_own_line(self, client, wire):
        wire.will_return(*reads(), created(3001, 14), created(3002, 13), created(3003, 12))

        review = await post(
            client,
            [finding(line=14), finding("Second point.", line=13, side=REMOVED),
             finding("Third point.", line=12, side=CONTEXT)],
        )

        assert review.count(POSTED) == 3
        posts = [request for request in wire.requests if request.method == "POST"]
        assert len(posts) == 3

    async def test_the_anchors_are_translated_per_finding(self, client, wire):
        import json

        wire.will_return(*reads(), created(3001, 14), created(3002, 13))

        await post(client, [finding(line=14), finding("Second.", line=13, side=REMOVED)])

        sent = [
            json.loads(request.content)["inline"]
            for request in wire.requests
            if request.method == "POST"
        ]
        assert sent == [{"path": FILE, "to": 14}, {"path": FILE, "from": 13}]

    async def test_one_finding_is_the_same_call_without_ceremony(self, client, wire):
        wire.will_return(*reads(), created(3001))

        review = await post(client, [finding()])

        assert review.all_landed
        assert review.count(POSTED) == 1

    async def test_an_empty_batch_is_refused(self, client, wire):
        with pytest.raises(BatchRefused):
            await post(client, [])

        assert not wire.called

    async def test_the_report_names_every_comment_it_created(self, client, wire):
        wire.will_return(*reads(), created(3001, 14), created(3002, 13))

        review = await post(client, [finding(line=14), finding("Second.", line=13, side=REMOVED)])

        assert "#3001" in review.to_markdown()
        assert "#3002" in review.to_markdown()


class TestNothingPostsUntilEverythingValidates:
    async def test_one_bad_anchor_refuses_the_whole_batch(self, client, wire):
        wire.will_return(*reads())

        with pytest.raises(BatchRefused) as caught:
            await post(client, [finding(line=14), finding("Second.", line=900)])

        assert "Nothing was posted" in str(caught.value)
        assert not any(request.method == "POST" for request in wire.requests)

    async def test_the_refusal_lists_every_problem_at_once(self, client, wire):
        wire.will_return(*reads())

        with pytest.raises(BatchRefused) as caught:
            await post(
                client,
                [finding(line=900), finding("Second.", line=901), finding("Third.", line=14)],
            )

        message = str(caught.value)
        assert "2 of 3" in message
        assert "Finding 1" in message and "Finding 2" in message
        assert "Finding 3" not in message

    async def test_a_path_not_in_the_diff_refuses_the_batch_too(self, client, wire):
        wire.will_return(*reads())
        stray = Finding.of(
            severity="LOW",
            category="x",
            message="y",
            anchor=Anchor(path="src/app/imagined.py", line=1, side=ADDED),
        )

        with pytest.raises(BatchRefused):
            await post(client, [finding(), stray])

        assert not any(request.method == "POST" for request in wire.requests)

    async def test_a_moved_basis_refuses_before_the_diff_is_even_read(self, client, wire):
        wire.will_return(httpx.Response(200, json=fixtures.pull_request(basis="aaaaaaaaaaaa")))

        with pytest.raises(BasisMoved):
            await post(client, [finding(), finding("Second.", line=13, side=REMOVED)])

        assert len(wire.requests) == 1


class TestWhenTheNetworkFailsHalfway:
    async def test_what_landed_is_reported_exactly(self, client, wire):
        wire.will_return(
            *reads(),
            created(3001, 14),
            httpx.Response(500, text="upstream is unwell"),
            created(3003, 12),
        )

        review = await post(
            client,
            [finding(line=14), finding("Second.", line=13, side=REMOVED),
             finding("Third.", line=12, side=CONTEXT)],
        )

        assert [outcome.status for outcome in review.outcomes] == [POSTED, FAILED, POSTED]
        assert "#3001" in review.to_markdown()

    async def test_nothing_already_posted_is_deleted(self, client, wire):
        wire.will_return(*reads(), created(3001), httpx.Response(500, text="no"))

        await post(client, [finding(), finding("Second.", line=13, side=REMOVED)])

        assert not any(request.method == "DELETE" for request in wire.requests)
        assert not any(request.method == "PUT" for request in wire.requests)

    async def test_a_rejected_credential_stops_the_rest_rather_than_hammering(
        self, client, wire
    ):
        wire.will_return(*reads(), httpx.Response(401, text=""), created(3002))

        review = await post(
            client, [finding(), finding("Second.", line=13, side=REMOVED)]
        )

        assert [outcome.status for outcome in review.outcomes] == [FAILED, NOT_ATTEMPTED]
        assert review.stopped_early

    async def test_the_report_says_which_ones_to_re_post(self, client, wire):
        wire.will_return(*reads(), httpx.Response(401, text=""), created(3002))

        review = await post(client, [finding(), finding("Second.", line=13, side=REMOVED)])

        assert "nothing is deleted" in review.to_markdown()
        assert "Re-post only" in review.to_markdown()

    async def test_an_orphaned_arrival_is_reported_as_such(self, client, wire):
        wire.will_return(*reads(), created(3001, orphaned=True))

        review = await post(client, [finding()])

        assert review.count(ORPHANED) == 1
        assert not review.all_landed


class TestSayingTheSameThingTwice:
    def existing(self, body: str, line: int = 14, comment_id: int = 900, deleted: bool = False):
        return {
            "values": [
                {
                    "id": comment_id,
                    "content": {"raw": body},
                    "user": {"account_id": OURS, "display_name": "Anwar Hossain"},
                    "created_on": "2026-08-22T09:14:22.000000+00:00",
                    "deleted": deleted,
                    "inline": {"path": FILE, "from": None, "to": line},
                }
            ]
        }

    async def test_the_same_point_at_the_same_line_is_skipped(self, client, wire):
        said = "**HIGH (note) · correctness**\n\n`RETRIES` is undefined here.\n\n---\n🤖 footer"
        wire.will_return(*reads(existing=self.existing(said)))

        review = await post(client, [finding()])

        assert review.outcomes[0].status == DUPLICATE
        assert "#900" in review.outcomes[0].detail
        assert not any(request.method == "POST" for request in wire.requests)

    async def test_a_colleagues_identical_point_counts_too(self, client, wire):
        theirs = self.existing("`RETRIES` is undefined here.")
        theirs["values"][0]["user"] = {"account_id": "someone-else", "display_name": "Nusrat"}
        wire.will_return(*reads(existing=theirs))

        review = await post(client, [finding()])

        assert review.outcomes[0].status == DUPLICATE

    async def test_the_same_point_at_a_different_line_still_posts(self, client, wire):
        wire.will_return(
            *reads(existing=self.existing("`RETRIES` is undefined here.", line=99)),
            created(3001),
        )

        review = await post(client, [finding()])

        assert review.outcomes[0].status == POSTED

    async def test_a_different_point_at_the_same_line_still_posts(self, client, wire):
        wire.will_return(
            *reads(existing=self.existing("Something else entirely.")),
            created(3001),
        )

        review = await post(client, [finding()])

        assert review.outcomes[0].status == POSTED

    async def test_whitespace_and_case_do_not_make_it_a_new_point(self, client, wire):
        wire.will_return(
            *reads(existing=self.existing("  `retries`   IS undefined   here.  "))
        )

        review = await post(client, [finding()])

        assert review.outcomes[0].status == DUPLICATE

    async def test_a_deleted_comment_does_not_block_a_repost(self, client, wire):
        wire.will_return(
            *reads(existing=self.existing("`RETRIES` is undefined here.", deleted=True)),
            created(3001),
        )

        review = await post(client, [finding()])

        assert review.outcomes[0].status == POSTED

    async def test_the_batch_does_not_repeat_itself_either(self, client, wire):
        wire.will_return(*reads(), created(3001))

        review = await post(client, [finding(), finding()])

        assert [outcome.status for outcome in review.outcomes] == [POSTED, DUPLICATE]


class TestOurStaleComments:
    async def test_they_are_surfaced_and_not_touched(self, client, wire):
        wire.will_return(*reads(existing=fixtures.comments()), created(3001))

        review = await post(client, [finding()])

        assert [comment.id for comment in review.stale_ours] == [1003]
        assert "1003" in review.to_markdown()
        assert not any(
            request.method in ("DELETE", "PUT") for request in wire.requests
        )

    async def test_the_report_says_why_they_are_still_there(self, client, wire):
        wire.will_return(*reads(existing=fixtures.comments()), created(3001))

        review = await post(client, [finding()])

        assert "may already have a reply" in review.to_markdown()

    async def test_somebody_elses_stale_comment_is_not_ours_to_report(self, client, wire):
        wire.will_return(*reads(existing=fixtures.comments()), created(3001))

        review = await post(client, [finding()], our_account=None)

        assert review.stale_ours == (), "with no identity, nothing is ours"

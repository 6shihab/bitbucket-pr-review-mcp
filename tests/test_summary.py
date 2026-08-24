"""One Summary Comment, updated in place, and never anybody else's.

Two properties carry this ticket. The server finds its own summary by a marker in the
comment body — the only state it keeps, deliberately stored where it cannot drift from
reality. And it will only ever update a comment it wrote, checked by re-reading the
comment from Bitbucket rather than trusting the id it was handed: a Caller reads pull
request descriptions, so a comment id in an argument is not evidence of anything.
"""

from __future__ import annotations

import json

import httpx
import pytest

from bitbucket_pr_review_mcp.findings import Reviewer
from bitbucket_pr_review_mcp.posting import BasisMoved
from bitbucket_pr_review_mcp.references import PullRequestRef
from bitbucket_pr_review_mcp.summary import (
    MARKER,
    MalformedSummary,
    NotOurComment,
    canonical,
    publish_summary,
    tally_of,
)

from . import fixtures

REF = PullRequestRef("streamstech", "db-explorer", 42)
BASIS = fixtures.BASIS
OURS = fixtures.OUR_ACCOUNT_ID
REVIEWER = Reviewer(display_name="Anwar Hossain", email="reviewer@streamstech.com")
VERDICT = "Two real problems in the retry path; the rest reads well."

COMMENTS = "/2.0/repositories/streamstech/db-explorer/pullrequests/42/comments"


def comment(
    comment_id: int,
    body: str,
    *,
    account_id: str = OURS,
    path: str | None = None,
    line: int = 14,
    deleted: bool = False,
    author: str = "Anwar Hossain",
) -> dict:
    payload = {
        "id": comment_id,
        "content": {"raw": body},
        "user": {"account_id": account_id, "display_name": author},
        "created_on": "2026-08-24T12:00:00.000000+00:00",
        "deleted": deleted,
    }
    if path:
        payload["inline"] = {"path": path, "from": None, "to": line}
    return payload


def finding_body(severity: str = "HIGH", message: str = "A point.") -> str:
    return f"**{severity} (warn) · correctness**\n\n{message}\n\n---\n🤖 footer"


def summary_body(verdict: str = "Earlier verdict.") -> str:
    return f"{MARKER}\n# Review summary\n\n{verdict}\n\n---\n🤖 footer"


def reads(*comments: dict, basis: str = BASIS):
    return [
        httpx.Response(200, json=fixtures.pull_request(basis=basis)),
        httpx.Response(200, json={"values": list(comments)}),
    ]


async def publish(client, *, comment_id=None, verdict=VERDICT, our_account=OURS, basis=BASIS):
    return await publish_summary(
        client, REF, verdict, basis, REVIEWER, our_account, comment_id
    )


def sent(wire) -> str:
    return json.loads(wire.last.content)["content"]["raw"]


class TestTheFirstReview:
    async def test_it_posts_a_summary_when_there_is_none(self, client, wire):
        wire.will_return(*reads(), httpx.Response(201, json=comment(5001, "x")))

        published = await publish(client)

        assert not published.updated
        assert wire.last.method == "POST"
        assert wire.last.url.path == COMMENTS

    async def test_the_verdict_is_the_callers_words(self, client, wire):
        wire.will_return(*reads(), httpx.Response(201, json=comment(5001, "x")))

        await publish(client)

        assert VERDICT in sent(wire)

    async def test_it_carries_the_marker_and_the_footer(self, client, wire):
        wire.will_return(*reads(), httpx.Response(201, json=comment(5001, "x")))

        await publish(client)

        body = sent(wire)
        assert MARKER in body
        assert "Machine-generated review summary" in body
        assert "Anwar Hossain" in body

    async def test_the_marker_sits_in_the_footer_where_it_reads_as_a_tool_name(
        self, client, wire
    ):
        """Bitbucket escapes HTML rather than dropping it, so an HTML comment would be
        visible angle brackets at the top — the kind of stray text a human deletes."""
        wire.will_return(*reads(), httpx.Response(201, json=comment(5001, "x")))

        await publish(client)

        body = sent(wire)
        assert body.startswith("# Review summary")
        assert "<!--" not in body
        assert body.index(MARKER) > body.index("---")

    async def test_it_is_not_an_inline_comment(self, client, wire):
        wire.will_return(*reads(), httpx.Response(201, json=comment(5001, "x")))

        await publish(client)

        assert "inline" not in json.loads(wire.last.content)

    @pytest.mark.parametrize("verdict", ["", "   ", "\n"])
    async def test_a_summary_with_nothing_in_it_is_refused(self, client, wire, verdict):
        with pytest.raises(MalformedSummary):
            await publish(client, verdict=verdict)

        assert not wire.called

    async def test_a_moved_basis_refuses_it(self, client, wire):
        wire.will_return(httpx.Response(200, json=fixtures.pull_request(basis="aaaaaaaaaaaa")))

        with pytest.raises(BasisMoved):
            await publish(client)

        assert not any(request.method in ("POST", "PUT") for request in wire.requests)


class TestTheTally:
    async def test_findings_are_counted_by_severity(self, client, wire):
        wire.will_return(
            *reads(
                comment(101, finding_body("HIGH"), path="a.py"),
                comment(102, finding_body("HIGH"), path="b.py"),
                comment(103, finding_body("LOW"), path="c.py"),
            ),
            httpx.Response(201, json=comment(5001, "x")),
        )

        published = await publish(client)

        assert published.tally["HIGH"] == 2
        assert published.tally["LOW"] == 1
        assert "| HIGH (warn) | 2 |" in sent(wire)

    async def test_severities_with_nothing_in_them_are_left_out(self, client, wire):
        wire.will_return(
            *reads(comment(101, finding_body("LOW"), path="a.py")),
            httpx.Response(201, json=comment(5001, "x")),
        )

        await publish(client)

        assert "LOW (note)" in sent(wire)
        assert "CRITICAL" not in sent(wire)

    async def test_it_counts_what_is_on_the_pull_request_not_what_it_was_told(self, client, wire):
        """A summary that disagrees with the page it heads is worse than none."""
        wire.will_return(
            *reads(comment(101, finding_body("HIGH"), path="a.py")),
            httpx.Response(201, json=comment(5001, "x")),
        )

        published = await publish(client)

        assert published.tally["HIGH"] == 1

    async def test_somebody_elses_comment_is_not_counted(self, client, wire):
        wire.will_return(
            *reads(comment(101, finding_body("HIGH"), path="a.py", account_id="theirs")),
            httpx.Response(201, json=comment(5001, "x")),
        )

        published = await publish(client)

        assert published.tally["HIGH"] == 0

    async def test_a_deleted_finding_is_not_counted(self, client, wire):
        wire.will_return(
            *reads(comment(101, finding_body("HIGH"), path="a.py", deleted=True)),
            httpx.Response(201, json=comment(5001, "x")),
        )

        published = await publish(client)

        assert published.tally["HIGH"] == 0

    async def test_no_findings_says_so_rather_than_showing_an_empty_table(self, client, wire):
        wire.will_return(*reads(), httpx.Response(201, json=comment(5001, "x")))

        await publish(client)

        assert "No findings were posted" in sent(wire)

    def test_the_summary_does_not_count_itself(self):
        from bitbucket_pr_review_mcp.comments import read_comment

        ours = (read_comment(comment(5001, summary_body()), OURS),)

        assert sum(tally_of(ours).values()) == 0


class TestTheSecondReview:
    async def test_it_updates_the_same_comment_in_place(self, client, wire):
        wire.will_return(
            *reads(comment(5001, summary_body())),
            httpx.Response(200, json=comment(5001, "updated")),
        )

        published = await publish(client)

        assert published.updated
        assert wire.last.method == "PUT"
        assert wire.last.url.path == f"{COMMENTS}/5001"

    async def test_nothing_new_is_posted(self, client, wire):
        wire.will_return(
            *reads(comment(5001, summary_body())),
            httpx.Response(200, json=comment(5001, "updated")),
        )

        await publish(client)

        assert not any(request.method == "POST" for request in wire.requests)

    async def test_the_marker_is_all_it_needs_to_find_it_again(self, client, wire):
        """No process state: a fresh server finds the summary the last one posted."""
        wire.will_return(
            *reads(comment(5001, summary_body())),
            httpx.Response(200, json=comment(5001, "updated")),
        )

        published = await publish(client)

        assert published.updated and published.comment.id == 5001

    async def test_a_deleted_summary_is_replaced_rather_than_written_into(self, client, wire):
        wire.will_return(
            *reads(comment(5001, summary_body(), deleted=True)),
            httpx.Response(201, json=comment(5002, "x")),
        )

        published = await publish(client)

        assert not published.updated
        assert wire.last.method == "POST"

    def test_the_newest_wins_if_an_older_format_left_two(self):
        from bitbucket_pr_review_mcp.comments import read_comment

        ours = tuple(
            read_comment(comment(number, summary_body()), OURS) for number in (5001, 5009)
        )

        assert canonical(ours).id == 5009

    def test_an_inline_comment_carrying_the_marker_is_not_the_summary(self):
        from bitbucket_pr_review_mcp.comments import read_comment

        planted = read_comment(comment(7, summary_body(), path="a.py"), OURS)

        assert canonical((planted,)) is None


class TestUpdatingSomebodyElses:
    """The rule ADR-0002 hangs the PUT on: authorship is re-read, never assumed."""

    async def test_an_id_is_re_read_before_anything_is_written(self, client, wire):
        wire.will_return(
            *reads(),
            httpx.Response(200, json=comment(9001, "theirs")),
            httpx.Response(200, json=comment(9001, "updated")),
        )

        await publish(client, comment_id=9001)

        methods = [request.method for request in wire.requests]
        assert methods[-2:] == ["GET", "PUT"], "read the author, then write"

    async def test_a_comment_somebody_else_wrote_is_refused(self, client, wire):
        wire.will_return(
            *reads(),
            httpx.Response(
                200, json=comment(9001, "theirs", account_id="theirs", author="Nusrat Jahan")
            ),
        )

        with pytest.raises(NotOurComment) as caught:
            await publish(client, comment_id=9001)

        assert "Nusrat Jahan" in str(caught.value)
        assert "Nothing was changed" in str(caught.value)
        assert not any(request.method == "PUT" for request in wire.requests)

    async def test_a_deleted_comment_is_refused(self, client, wire):
        wire.will_return(*reads(), httpx.Response(200, json=comment(9001, "", deleted=True)))

        with pytest.raises(NotOurComment) as caught:
            await publish(client, comment_id=9001)

        assert "deleted" in str(caught.value)
        assert not any(request.method == "PUT" for request in wire.requests)

    async def test_an_id_that_matches_our_display_name_but_not_our_account_is_refused(
        self, client, wire
    ):
        wire.will_return(
            *reads(),
            httpx.Response(
                200,
                json=comment(9001, summary_body(), account_id="theirs", author="Anwar Hossain"),
            ),
        )

        with pytest.raises(NotOurComment):
            await publish(client, comment_id=9001)

    async def test_without_knowing_who_we_are_it_refuses_to_write_at_all(self, client, wire):
        with pytest.raises(NotOurComment) as caught:
            await publish(client, our_account=None)

        assert "read:user:bitbucket" in str(caught.value)
        assert not wire.called

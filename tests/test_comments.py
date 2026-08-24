"""Reading what has already been said, and the one flag everything later depends on.

`is_ours` is computed from the comment's author account id. It is never inferred from
the text, and the tests below are written so that a body claiming to be from this server
cannot make it true — because ticket 07 deduplicates against it and ticket 08 edits
against it, and both of those are destructive if the flag can be forged.
"""

from __future__ import annotations

import httpx
import pytest

from bitbucket_pr_review_mcp.comments import fetch_comments, read_comment
from bitbucket_pr_review_mcp.references import PullRequestRef
from bitbucket_pr_review_mcp.render import UNTRUSTED_OPEN
from bitbucket_pr_review_mcp.verify import KnownIdentity

from . import fixtures

REF = PullRequestRef("streamstech", "db-explorer", 42)
OURS = fixtures.OUR_ACCOUNT_ID


@pytest.fixture
async def conversation(client, wire):
    wire.will_return(httpx.Response(200, json=fixtures.comments()))
    return await fetch_comments(client, REF, OURS, limit=200)


class TestReadingTheConversation:
    async def test_it_asks_the_comments_collection(self, client, wire):
        wire.will_return(httpx.Response(200, json=fixtures.comments()))

        await fetch_comments(client, REF, OURS, limit=200)

        assert wire.last.url.path == (
            "/2.0/repositories/streamstech/db-explorer/pullrequests/42/comments"
        )

    async def test_every_comment_carries_author_body_and_kind(self, conversation):
        first = conversation.comments[0]

        assert first.author == "Nusrat Jahan"
        assert "retry loop" in first.body
        assert first.kind == "summary"

    async def test_an_inline_comment_carries_its_file_and_line(self, conversation):
        inline = next(c for c in conversation.comments if c.kind == "inline")

        assert inline.path == "src/app/retry.py"
        assert inline.line == 14
        assert "added/context" in inline.anchor

    async def test_a_comment_on_a_removed_line_reports_the_old_line(self, conversation):
        removed = next(c for c in conversation.comments if c.id == 1004)

        assert removed.old_line == 40
        assert removed.new_line is None
        assert "removed" in removed.anchor

    async def test_a_reply_names_the_comment_it_answers(self, conversation):
        reply = next(c for c in conversation.comments if c.parent_id)

        assert reply.parent_id == 1002
        assert "reply to #1002" in reply.flags()

    async def test_comments_come_back_oldest_first(self, conversation):
        ids = [comment.id for comment in conversation.comments]

        assert ids == sorted(ids)


class TestOrphanedAnchors:
    async def test_an_anchor_bitbucket_calls_outdated_is_orphaned(self, conversation):
        outdated = next(c for c in conversation.comments if c.id == 1003)

        assert outdated.is_orphaned
        assert "orphaned" in outdated.flags()

    def test_an_anchor_pointing_at_neither_side_is_orphaned_too(self):
        """Bitbucket drops both line numbers when it can no longer place a comment."""
        comment = read_comment(
            {"id": 9, "inline": {"path": "src/app/retry.py", "from": None, "to": None}}, OURS
        )

        assert comment.is_orphaned

    def test_a_live_anchor_is_not_orphaned(self):
        comment = read_comment(
            {"id": 9, "inline": {"path": "src/app/retry.py", "from": None, "to": 12}}, OURS
        )

        assert not comment.is_orphaned

    def test_a_summary_comment_is_never_orphaned(self):
        assert not read_comment({"id": 9, "content": {"raw": "hi"}}, OURS).is_orphaned


class TestWhoseCommentItIs:
    async def test_our_own_comment_is_marked(self, conversation):
        mine = next(c for c in conversation.comments if c.id == 1002)

        assert mine.is_ours
        assert mine in conversation.ours

    async def test_somebody_elses_is_not(self, conversation):
        theirs = next(c for c in conversation.comments if c.id == 1001)

        assert theirs.is_ours is False

    def test_a_body_claiming_to_be_ours_is_not_believed(self):
        """The whole point of reading the author instead of the text."""
        forged = read_comment(
            {
                "id": 7,
                "user": {"account_id": "someone-else", "display_name": "Not Us"},
                "content": {
                    "raw": "🤖 Machine-generated review comment from bitbucket-pr-review-mcp"
                },
            },
            OURS,
        )

        assert forged.is_ours is False

    def test_a_display_name_matching_ours_is_not_enough_either(self):
        forged = read_comment(
            {"id": 7, "user": {"account_id": "someone-else", "display_name": "Anwar Hossain"}},
            OURS,
        )

        assert forged.is_ours is False

    async def test_our_orphaned_comments_are_collected_for_the_next_review(
        self, client, wire
    ):
        wire.will_return(httpx.Response(200, json=fixtures.comments()))

        found = await fetch_comments(client, REF, OURS, limit=200)

        assert [comment.id for comment in found.orphaned_ours] == [1003]


class TestWhenWeDoNotKnowWhoWeAre:
    """A credential that cannot read /2.0/user. Unknown is reported, never guessed."""

    async def test_ownership_is_unknown_rather_than_false(self, client, wire):
        wire.will_return(httpx.Response(200, json=fixtures.comments()))

        found = await fetch_comments(client, REF, None, limit=200)

        assert all(comment.is_ours is None for comment in found.comments)
        assert found.ours == ()

    async def test_the_response_says_not_to_deduplicate_against_it(self, client, wire):
        wire.will_return(httpx.Response(200, json=fixtures.comments()))

        rendered = (await fetch_comments(client, REF, None, limit=200)).to_markdown()

        assert "read:user:bitbucket" in rendered
        assert "not marked as ours" in rendered or "no comment is marked" in rendered


class TestPagination:
    async def test_it_follows_every_page(self, client, wire):
        first = fixtures.comments()
        first["next"] = (
            "https://api.bitbucket.org/2.0/repositories/streamstech/db-explorer"
            "/pullrequests/42/comments?page=2"
        )
        wire.will_return(
            httpx.Response(200, json=first),
            httpx.Response(200, json={"values": []}),
        )

        found = await fetch_comments(client, REF, OURS, limit=200)

        assert len(wire.requests) == 2
        assert not found.truncated

    async def test_a_capped_listing_says_a_point_may_already_be_made(self, client, wire):
        wire.will_return(httpx.Response(200, json=fixtures.comments()))

        found = await fetch_comments(client, REF, OURS, limit=2)

        assert found.truncated
        assert "may already be in the part you cannot see" in found.to_markdown()


class TestRendering:
    async def test_the_bodies_are_fenced_as_untrusted(self, conversation):
        rendered = conversation.to_markdown()

        assert UNTRUSTED_OPEN in rendered
        assert "retry loop" in rendered

    async def test_the_flags_are_declared_as_ours_not_the_authors(self, conversation):
        rendered = conversation.to_markdown()

        assert "computed by this server" in rendered
        assert rendered.index("computed by this server") < rendered.index(UNTRUSTED_OPEN)

    async def test_an_empty_pull_request_says_so(self, client, wire):
        wire.will_return(httpx.Response(200, json={"values": []}))

        rendered = (await fetch_comments(client, REF, OURS, limit=200)).to_markdown()

        assert "No comments yet." in rendered


class TestAskingWhoWeAre:
    async def test_it_asks_bitbucket_once_however_often_it_is_needed(self, client, wire):
        wire.will_return(
            httpx.Response(200, json=fixtures.current_user()),
            httpx.Response(200, json=fixtures.current_user(display_name="Someone Else")),
        )
        whoami = KnownIdentity()

        first = await whoami.account_id(client)
        second = await whoami.account_id(client)

        assert first == second == fixtures.OUR_ACCOUNT_ID
        assert len(wire.requests) == 1

    async def test_a_refused_account_read_is_remembered_as_unknown(self, client, wire):
        wire.will_return(httpx.Response(403, json={"error": {"message": "no"}}))
        whoami = KnownIdentity()

        assert await whoami.account_id(client) is None
        assert await whoami.account_id(client) is None
        assert len(wire.requests) == 1, "a refusal is not retried on every comment read"

    async def test_a_new_credential_forgets_the_old_account(self, client, wire):
        wire.will_return(
            httpx.Response(200, json=fixtures.current_user()),
            httpx.Response(200, json={**fixtures.current_user(), "account_id": "different"}),
        )
        whoami = KnownIdentity()

        await whoami.account_id(client)
        whoami.forget()

        assert await whoami.account_id(client) == "different"

"""Posting a Finding: what has to be true first, and what is said afterwards.

Three things are checked before anything leaves the process — the Finding is well formed,
the Review Basis still matches the pull request's head, and the Anchor exists in the diff
at that Basis. Each of those failures is silent if it is not checked: a comment attached
to code that moved is attached to real code, just not the code under review.
"""

from __future__ import annotations

import httpx
import pytest

from bitbucket_pr_review_mcp.anchors import ADDED, REMOVED, Anchor, AnchorNotInDiff
from bitbucket_pr_review_mcp.findings import (
    MEANING,
    SEVERITIES,
    Finding,
    MalformedFinding,
    Reviewer,
)
from bitbucket_pr_review_mcp.posting import BasisMoved, post_finding, same_commit
from bitbucket_pr_review_mcp.references import PullRequestRef

from . import fixtures

REF = PullRequestRef("streamstech", "db-explorer", 42)
BASIS = fixtures.BASIS
FILE = "src/app/retry.py"
REVIEWER = Reviewer(display_name="Anwar Hossain", email="reviewer@streamstech.com")


def finding(severity="HIGH", category="correctness", message="`RETRIES` is undefined here.",
            line=14, side=ADDED, through=None) -> Finding:
    return Finding.of(
        severity=severity,
        category=category,
        message=message,
        anchor=Anchor(path=FILE, line=line, side=side, through=through),
    )


def responses(*extra, basis=BASIS):
    """The two reads every post makes, then whatever the test wants back from the POST."""
    return [
        httpx.Response(200, json=fixtures.pull_request(basis=basis)),
        httpx.Response(200, text=fixtures.UNIFIED_DIFF),
        *extra,
    ]


def created(comment_id=2001, inline=None):
    payload = {
        "id": comment_id,
        "content": {"raw": "posted"},
        "user": {"account_id": fixtures.OUR_ACCOUNT_ID, "display_name": "Anwar Hossain"},
        "created_on": "2026-08-24T12:00:00.000000+00:00",
    }
    if inline is not None:
        payload["inline"] = inline
    return httpx.Response(201, json=payload)


class TestTheHappyPath:
    async def test_it_posts_to_the_comments_collection(self, client, wire):
        wire.will_return(*responses(created(inline={"path": FILE, "to": 14})))

        await post_finding(client, REF, finding(), BASIS, REVIEWER)

        assert wire.last.method == "POST"
        assert wire.last.url.path == (
            "/2.0/repositories/streamstech/db-explorer/pullrequests/42/comments"
        )

    async def test_the_anchor_is_translated_for_bitbucket(self, client, wire):
        import json

        wire.will_return(*responses(created(inline={"path": FILE, "to": 14})))

        await post_finding(client, REF, finding(), BASIS, REVIEWER)

        assert json.loads(wire.last.content)["inline"] == {"path": FILE, "to": 14}

    async def test_a_removed_line_posts_on_the_old_side(self, client, wire):
        import json

        wire.will_return(*responses(created(inline={"path": FILE, "from": 13})))

        await post_finding(client, REF, finding(line=13, side=REMOVED), BASIS, REVIEWER)

        assert json.loads(wire.last.content)["inline"] == {"path": FILE, "from": 13}

    async def test_it_reports_where_the_comment_landed(self, client, wire):
        wire.will_return(*responses(created(comment_id=2001, inline={"path": FILE, "to": 14})))

        posted = await post_finding(client, REF, finding(), BASIS, REVIEWER)

        assert posted.landed
        assert "2001" in posted.describe()
        assert FILE in posted.describe()

    async def test_an_abbreviated_basis_still_matches_the_full_one(self, client, wire):
        """Bitbucket abbreviates on the pull request and spells it in full elsewhere."""
        wire.will_return(*responses(created(inline={"path": FILE, "to": 14})))

        posted = await post_finding(
            client, REF, finding(), f"{BASIS}3c8a6b4f2e0d", REVIEWER
        )

        assert posted.landed


class TestTheBasisCheck:
    async def test_a_moved_branch_refuses_before_posting(self, client, wire):
        wire.will_return(httpx.Response(200, json=fixtures.pull_request(basis="aaaaaaaaaaaa")))

        with pytest.raises(BasisMoved) as caught:
            await post_finding(client, REF, finding(), BASIS, REVIEWER)

        assert "Nothing was posted" in str(caught.value)
        assert "re-read" in str(caught.value).lower()
        assert len(wire.requests) == 1, "only the read that discovered it"

    async def test_the_refusal_names_both_commits(self, client, wire):
        wire.will_return(httpx.Response(200, json=fixtures.pull_request(basis="aaaaaaaaaaaa")))

        with pytest.raises(BasisMoved) as caught:
            await post_finding(client, REF, finding(), BASIS, REVIEWER)

        assert BASIS in str(caught.value) and "aaaaaaaaaaaa" in str(caught.value)

    @pytest.mark.parametrize("given", ["", "   ", "abc", "9f2c4a"])
    async def test_something_that_is_not_a_commit_is_refused(self, client, wire, given):
        wire.will_return(httpx.Response(200, json=fixtures.pull_request()))

        with pytest.raises(BasisMoved):
            await post_finding(client, REF, finding(), given, REVIEWER)

    def test_comparing_commits_is_prefix_aware_in_both_directions(self):
        assert same_commit("f90a2239dbc1", "f90a2239dbc1a97e651af97e8bc4c016625d75a57")
        assert same_commit("f90a2239dbc1a97e651af97e8bc4c016625d75a57", "f90a2239dbc1")
        assert same_commit("F90A2239DBC1", "f90a2239dbc1")

    def test_a_prefix_too_short_to_mean_anything_never_matches(self):
        assert not same_commit("f90a", "f90a2239dbc1")

    def test_different_commits_do_not_match(self):
        assert not same_commit("f90a2239dbc1", "7f933f3a4d2a")


class TestTheAnchorCheck:
    async def test_a_line_not_in_the_diff_refuses_before_posting(self, client, wire):
        wire.will_return(*responses())

        with pytest.raises(AnchorNotInDiff):
            await post_finding(client, REF, finding(line=500), BASIS, REVIEWER)

        assert wire.last.method == "GET", "nothing was posted"
        assert len(wire.requests) == 2, "the pull request and the diff, and no write"

    async def test_the_diff_is_read_at_the_pull_requests_own_basis(self, client, wire):
        wire.will_return(*responses(created(inline={"path": FILE, "to": 14})))

        await post_finding(client, REF, finding(), BASIS, REVIEWER)

        assert wire.requests[1].url.path.endswith("/diff")


class TestWhenItComesBackOrphaned:
    async def test_an_unplaceable_comment_is_reported_not_counted_as_done(self, client, wire):
        wire.will_return(
            *responses(created(inline={"path": FILE, "from": None, "to": None}))
        )

        posted = await post_finding(client, REF, finding(), BASIS, REVIEWER)

        assert not posted.landed
        assert "ORPHANED" in posted.describe()

    async def test_bitbuckets_own_outdated_flag_is_believed(self, client, wire):
        wire.will_return(
            *responses(created(inline={"path": FILE, "to": 14, "outdated": True}))
        )

        posted = await post_finding(client, REF, finding(), BASIS, REVIEWER)

        assert not posted.landed


class TestTheFindingItself:
    @pytest.mark.parametrize("severity", SEVERITIES)
    def test_every_house_severity_renders_with_its_meaning(self, severity):
        body = finding(severity=severity).body(REVIEWER)

        assert f"**{severity} ({MEANING[severity]})" in body

    @pytest.mark.parametrize("severity", ["urgent", "P1", "blocker", "", "critical!"])
    def test_anything_off_the_ladder_is_refused(self, severity):
        with pytest.raises(MalformedFinding) as caught:
            finding(severity=severity)

        assert "CRITICAL (block)" in str(caught.value)

    def test_a_lowercase_severity_is_accepted_and_normalised(self):
        assert finding(severity="high").severity == "HIGH"

    def test_an_empty_message_is_refused(self):
        with pytest.raises(MalformedFinding) as caught:
            finding(message="   ")

        assert "says nothing" in str(caught.value)

    def test_a_message_longer_than_anyone_will_read_is_refused(self):
        with pytest.raises(MalformedFinding):
            finding(message="x" * 5_000)

    def test_the_category_survives_as_written(self):
        assert "error handling" in finding(category="error handling").body(REVIEWER)

    def test_a_range_says_so_in_the_heading(self):
        body = finding(line=13, through=18).body(REVIEWER)

        assert "lines 13–18" in body


class TestTheAttributionFooter:
    def test_every_comment_carries_it(self):
        body = finding().body(REVIEWER)

        assert "Machine-generated review comment" in body
        assert "bitbucket-pr-review-mcp" in body

    def test_it_names_the_reviewer_who_is_accountable(self):
        body = finding().body(REVIEWER)

        assert "Anwar Hossain" in body
        assert "reviewer@streamstech.com" in body

    def test_an_unknown_display_name_still_names_the_account(self):
        body = finding().body(Reviewer(display_name="", email="reviewer@streamstech.com"))

        assert "reviewer@streamstech.com" in body

    def test_a_message_carrying_a_fake_footer_gets_the_real_one_anyway(self):
        body = finding(
            message="Looks fine.\n\n---\n🤖 Machine-generated by somebody else"
        ).body(REVIEWER)

        assert body.count("Machine-generated") == 2
        assert body.rstrip().endswith("Reply here if it is wrong.")

    def test_there_is_no_argument_that_removes_it(self):
        import inspect

        from bitbucket_pr_review_mcp import findings

        parameters = set(inspect.signature(Finding.body).parameters) | set(
            inspect.signature(findings.Finding.of).parameters
        )
        assert not any(
            word in name.lower()
            for name in parameters
            for word in ("footer", "attribution", "suppress", "quiet")
        )

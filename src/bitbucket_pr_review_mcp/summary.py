"""One Summary Comment per Pull Request, updated in place rather than stacked.

A human should read the verdict before the line-by-line detail, and on the third review
of the same branch they should still find one summary rather than five stale ones. That
requires knowing which comment is ours to update — and the answer deliberately lives in
the comment itself, as a marker embedded in the body. This server keeps no database, and
the one piece of state it needs is stored where it can never disagree with reality.

Updating is the only write besides posting that ADR-0002 permits, and it is bounded by
the rule that matters: authorship is verified by **re-reading the comment from
Bitbucket**, never by trusting an id the Caller passed. A mistaken argument — or a
malicious one, since Callers read pull request descriptions — must not be able to
overwrite a colleague's words.

The tally is counted from what is actually on the Pull Request, not from what the Caller
says it posted. The Caller writes the verdict; this server counts and lays out. That
split is ADR-0001.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .client import BitbucketClient
from .comments import Comment, comments_path, fetch_comments, read_comment
from .findings import MEANING, SEVERITIES, Reviewer, footer
from .posting import check_basis
from .pullrequests import fetch_pull_request
from .references import PullRequestRef
from .render import untrusted

# Stable, and versioned so a later format can recognise an earlier one. It lives inside
# the Attribution Footer rather than in an HTML comment: Bitbucket escapes HTML instead
# of dropping it, so `<!-- ... -->` renders as visible angle brackets at the top of the
# comment — stray-looking text that invites tidying away. Learned by posting one.
MARKER = "bitbucket-pr-review-mcp:summary/1"

# Our own inline comments open with this heading, which is how the tally is counted.
_SEVERITY_HEADING = re.compile(r"^\*\*(?P<severity>[A-Z]+) \(", re.MULTILINE)

MAX_VERDICT = 8_000


class NotOurComment(PermissionError):
    """The comment belongs to somebody else, or no longer exists. Nothing was written."""


class MalformedSummary(ValueError):
    """The summary cannot be posted as given."""


@dataclass(frozen=True, slots=True)
class Summary:
    """The Review as a whole: the Caller's verdict, and this server's count of it."""

    ref: PullRequestRef
    basis: str
    verdict: str
    tally: dict[str, int]
    stale: tuple[Comment, ...]

    @property
    def total(self) -> int:
        return sum(self.tally.values())

    def body(self, reviewer: Reviewer) -> str:
        parts = [
            "# Review summary",
            "",
            self.verdict.strip(),
            "",
            "## What was found",
            "",
            self._table(),
            "",
            f"Reviewed at Review Basis `{self.basis}`.",
        ]

        if self.stale:
            parts += [
                "",
                "### Earlier comments of mine that no longer match the code",
                "",
                "Left in place — one of them may already have a reply.",
                "",
                *(f"- #{comment.id} on `{comment.anchor}`" for comment in self.stale),
            ]

        parts += ["", footer(reviewer, kind="review summary", marker=MARKER)]
        return "\n".join(parts)

    def _table(self) -> str:
        if not self.total:
            return "No findings were posted against this pull request."

        rows = [
            f"| {severity} ({MEANING[severity]}) | {self.tally.get(severity, 0)} |"
            for severity in SEVERITIES
            if self.tally.get(severity)
        ]
        return "\n".join(["| Severity | Findings |", "|---|---|", *rows])


@dataclass(frozen=True, slots=True)
class Published:
    """What happened to the Summary Comment."""

    comment: Comment
    updated: bool
    tally: dict[str, int]

    def to_markdown(self) -> str:
        verb = "Updated" if self.updated else "Posted"
        counted = ", ".join(
            f"{count} {severity}" for severity, count in self.tally.items() if count
        )
        return "\n".join(
            [
                f"# {verb} the review summary — comment #{self.comment.id}",
                "",
                f"It counts {counted or 'no findings'} currently on the pull request.",
                "",
                (
                    "There is one summary comment on this pull request, and the next "
                    "review will update this same one rather than adding another. It is "
                    "found by a marker in its body, so that holds across restarts."
                ),
            ]
        )


async def publish_summary(
    client: BitbucketClient,
    ref: PullRequestRef,
    verdict: str,
    basis: str,
    reviewer: Reviewer,
    our_account_id: str | None,
    comment_id: int | None = None,
    comment_limit: int = 200,
) -> Published:
    """Post the Summary Comment, or update the one already there. Never both, never many."""
    said = (verdict or "").strip()
    if not said:
        raise MalformedSummary("A summary with no verdict says nothing. Write the verdict.")
    if len(said) > MAX_VERDICT:
        raise MalformedSummary(
            f"This summary is {len(said)} characters. Keep it under {MAX_VERDICT}: the "
            "detail belongs on the lines it is about."
        )
    if our_account_id is None:
        raise NotOurComment(
            "This server cannot read its own account, so it cannot tell its summary from "
            "anybody else's and will not update a comment on a guess. The credential "
            "needs read:user:bitbucket; run `uv run bb-pr-mcp --setup` to replace it."
        )

    pull_request = await fetch_pull_request(client, ref)
    check_basis(basis, pull_request)

    existing = await fetch_comments(client, ref, our_account_id, comment_limit)
    summary = Summary(
        ref=ref,
        basis=pull_request.review_basis,
        verdict=said,
        tally=tally_of(existing.ours),
        stale=existing.orphaned_ours,
    )
    payload = {"content": {"raw": summary.body(reviewer)}}

    target = (
        await _verified(client, ref, comment_id, our_account_id)
        if comment_id is not None
        else canonical(existing.ours)
    )

    if target is None:
        response = await client.request("POST", comments_path(ref), json=payload)
        return Published(
            comment=read_comment(response.json(), our_account_id),
            updated=False,
            tally=summary.tally,
        )

    response = await client.request("PUT", f"{comments_path(ref)}/{target.id}", json=payload)
    return Published(
        comment=read_comment(response.json(), our_account_id),
        updated=True,
        tally=summary.tally,
    )


def canonical(ours: tuple[Comment, ...]) -> Comment | None:
    """Our Summary Comment, if this Pull Request already has one.

    Recognised by the marker in the body and by being ours — both, because either alone
    is wrong: anybody can paste the marker into a comment, and we post plenty of comments
    that are not summaries. The newest wins if an older format ever left two.
    """
    found = [
        comment
        for comment in ours
        if not comment.path and MARKER in comment.body and not comment.is_deleted
    ]
    return max(found, key=lambda comment: comment.id) if found else None


def tally_of(ours: tuple[Comment, ...]) -> dict[str, int]:
    """Count the Findings actually sitting on the Pull Request, by Severity.

    Counted from the comments rather than from what the Caller says it posted: a summary
    that disagrees with the page it is at the top of is worse than no summary.
    """
    counted = dict.fromkeys(SEVERITIES, 0)
    for comment in ours:
        if not comment.path or MARKER in comment.body:
            continue
        heading = _SEVERITY_HEADING.search(comment.body)
        if heading and heading.group("severity") in counted:
            counted[heading.group("severity")] += 1
    return counted


async def _verified(
    client: BitbucketClient,
    ref: PullRequestRef,
    comment_id: int,
    our_account_id: str,
) -> Comment:
    """Re-read the comment and check who wrote it. The id alone proves nothing."""
    response = await client.request("GET", f"{comments_path(ref)}/{comment_id}")
    comment = read_comment(response.json(), our_account_id)

    if comment.is_deleted:
        raise NotOurComment(
            f"Comment #{comment_id} has been deleted. Bitbucket keeps the id after the "
            "body is gone; post a new summary rather than writing into it."
        )
    if not comment.is_ours:
        raise NotOurComment(
            f"Comment #{comment_id} was written by {comment.author}, not by this server. "
            "Nothing was changed. This server updates only comments it wrote — reply to "
            "somebody else's comment, never overwrite it."
        )
    return comment


def summary_text(comment: Comment) -> str:
    """The Summary Comment as a Caller should read it: fenced, like everything fetched."""
    return untrusted(comment.body)

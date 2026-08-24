"""Posting a whole Review at once, and behaving on the third pass over the same code.

The batch is not about round trips. It is about where a partial write can come from: if
every Anchor is checked against the hunks and the Review Basis is re-checked before the
first POST, then a half-posted review can only be the network's doing, never bad input.
So validation is a phase, not a per-comment step, and nothing is sent until all of it
passes.

When the network does fail halfway, the answer is an honest per-comment report and
**no rollback**. Deleting comments a human may already have read — and may already have
replied to — is worse than a list saying exactly what landed. ADR-0002 permits no DELETE
anywhere in this server, and this is the case that would have tempted it.

Re-review hygiene is the other half. A Finding identical to one already sitting at that
path and line is refused rather than posted again, and our own comments whose Anchors
have gone stale are surfaced so the Caller can acknowledge them — and left exactly where
they are, because a stale comment may have a colleague's reply hanging off it.
"""

from __future__ import annotations

from dataclasses import dataclass

from .anchors import Anchored, AnchorNotInDiff, anchor_in
from .client import BitbucketClient, BitbucketError, Unauthorized
from .comments import Comment, comments_path, fetch_comments, read_comment
from .diffs import DiffCache, PathNotInDiff, fetch_diff
from .findings import Finding, Reviewer
from .posting import check_basis
from .pullrequests import fetch_pull_request
from .references import PullRequestRef
from .render import field_table, notice

POSTED = "posted"
ORPHANED = "posted, orphaned"
DUPLICATE = "skipped, already said"
FAILED = "failed"
NOT_ATTEMPTED = "not attempted"


class BatchRefused(ValueError):
    """Nothing was posted. The message lists every reason, so one call fixes all of them."""


@dataclass(frozen=True, slots=True)
class Outcome:
    """What happened to one Finding in the batch."""

    finding: Finding
    status: str
    comment: Comment | None = None
    detail: str = ""

    @property
    def landed(self) -> bool:
        return self.status == POSTED

    def row(self) -> str:
        where = self.finding.anchor.describe() if self.finding.anchor else "the pull request"
        reference = f"#{self.comment.id}" if self.comment else "—"
        return f"| {self.status} | {reference} | `{where}` | {self.detail or '—'} |"


@dataclass(frozen=True, slots=True)
class ReviewPosted:
    """The whole batch's result, and what the Caller should do about it."""

    ref: PullRequestRef
    outcomes: tuple[Outcome, ...]
    stale_ours: tuple[Comment, ...] = ()
    stopped_early: str = ""

    def count(self, status: str) -> int:
        return sum(1 for outcome in self.outcomes if outcome.status == status)

    @property
    def all_landed(self) -> bool:
        return all(outcome.landed for outcome in self.outcomes)

    def to_markdown(self) -> str:
        lines = [
            f"# Review posted to {self.ref}",
            "",
            field_table(
                [
                    ("Posted", str(self.count(POSTED))),
                    ("Orphaned on arrival", str(self.count(ORPHANED))),
                    ("Skipped as duplicates", str(self.count(DUPLICATE))),
                    ("Failed", str(self.count(FAILED) + self.count(NOT_ATTEMPTED))),
                ]
            ),
            "",
        ]

        if self.stopped_early:
            lines += [
                notice(
                    f"The batch stopped part way: {self.stopped_early} Everything already "
                    "posted is listed below and has been left where it is — nothing is "
                    "deleted to tidy up a failure. Re-post only the ones marked "
                    f"'{FAILED}' or '{NOT_ATTEMPTED}'."
                ),
                "",
            ]

        lines += [
            "| Result | Comment | Anchor | Note |",
            "|---|---|---|---|",
            *(outcome.row() for outcome in self.outcomes),
        ]

        if self.stale_ours:
            lines += [
                "",
                "## Earlier comments of ours that no longer match the code",
                "",
                "These were left standing on purpose: one of them may already have a "
                "reply. Acknowledge them in your summary rather than expecting them to "
                "disappear.",
                "",
                *(
                    f"- #{comment.id} on `{comment.anchor}`"
                    for comment in self.stale_ours
                ),
            ]

        return "\n".join(lines)


async def post_review(
    client: BitbucketClient,
    ref: PullRequestRef,
    findings: list[Finding],
    basis: str,
    reviewer: Reviewer,
    our_account_id: str | None,
    cache: DiffCache | None = None,
    comment_limit: int = 200,
) -> ReviewPosted:
    """Validate everything, then post what is left, then say exactly what happened."""
    if not findings:
        raise BatchRefused("There is nothing to post. Give at least one finding.")

    pull_request = await fetch_pull_request(client, ref)
    check_basis(basis, pull_request)  # the whole batch, or none of it

    diff = await fetch_diff(client, ref, pull_request.review_basis, cache)
    anchored = _validate(findings, diff)

    existing = await fetch_comments(client, ref, our_account_id, comment_limit)
    outcomes: list[Outcome] = []
    stopped = ""
    already = _said_already(existing.comments)

    for finding, place in zip(findings, anchored, strict=True):
        if stopped:
            outcomes.append(Outcome(finding, NOT_ATTEMPTED))
            continue

        duplicate = already.get(_fingerprint(finding))
        if duplicate is not None:
            outcomes.append(
                Outcome(
                    finding,
                    DUPLICATE,
                    detail=f"the same point is already comment #{duplicate}",
                )
            )
            continue

        outcome, stopped = await _post_one(client, ref, finding, place, reviewer)
        outcomes.append(outcome)
        if outcome.comment is not None:
            already[_fingerprint(finding)] = outcome.comment.id

    return ReviewPosted(
        ref=ref,
        outcomes=tuple(outcomes),
        stale_ours=existing.orphaned_ours,
        stopped_early=stopped,
    )


def _validate(findings: list[Finding], diff) -> list[Anchored | None]:
    """Every Anchor, before any POST. One bad one refuses the batch, listing them all."""
    places: list[Anchored | None] = []
    problems: list[str] = []

    for position, finding in enumerate(findings, start=1):
        if finding.anchor is None:
            places.append(None)
            continue
        try:
            places.append(anchor_in(diff, finding.anchor))
        except (AnchorNotInDiff, PathNotInDiff) as exc:
            places.append(None)
            problems.append(f"Finding {position} ({finding.anchor.describe()}): {exc}")

    if problems:
        raise BatchRefused(
            "Nothing was posted. "
            f"{len(problems)} of {len(findings)} findings name a place this diff does "
            "not have, and a batch that posted the good ones would leave you guessing "
            "which. Fix these and call again:\n\n" + "\n\n".join(problems)
        )
    return places


async def _post_one(
    client: BitbucketClient,
    ref: PullRequestRef,
    finding: Finding,
    place: Anchored | None,
    reviewer: Reviewer,
) -> tuple[Outcome, str]:
    """Post one comment of a validated batch. Returns the outcome and any stop reason."""
    payload: dict[str, object] = {"content": {"raw": finding.body(reviewer)}}
    if place is not None:
        payload["inline"] = place.inline()

    try:
        response = await client.request("POST", comments_path(ref), json=payload)
    except Unauthorized as exc:
        return Outcome(finding, FAILED, detail=str(exc)), "the credential was rejected."
    except BitbucketError as exc:
        return Outcome(finding, FAILED, detail=str(exc)), ""

    comment = read_comment(response.json(), our_account_id=None)
    status = ORPHANED if comment.is_orphaned else POSTED
    detail = "Bitbucket could not place it against the current code" if comment.is_orphaned else ""
    return Outcome(finding, status, comment=comment, detail=detail), ""


def _said_already(comments: tuple[Comment, ...]) -> dict[tuple, int]:
    """Index what is already on the pull request, by where it is and what it says.

    Anyone's comment counts, not only ours: repeating a point a colleague already made
    is the same noise, and the Caller cannot tell the difference from the reader's side.
    """
    found: dict[tuple, int] = {}
    for comment in comments:
        if comment.is_deleted or not comment.path:
            continue
        key = (comment.path, comment.line, _normalise(comment.body))
        found.setdefault(key, comment.id)
    return found


def _fingerprint(finding: Finding) -> tuple:
    """What makes a Finding the same point as an existing comment: place and words."""
    anchor = finding.anchor
    if anchor is None:
        return ("", None, _normalise(finding.message))

    line = (anchor.through or anchor.line) if anchor.is_range else anchor.line
    return (anchor.path, line, _normalise(finding.message))


def _normalise(text: str) -> str:
    """Compare what was said, not how it was spaced — and ignore our own furniture.

    An existing comment of ours carries a severity heading and the Attribution Footer;
    the Finding being posted carries neither yet. Comparing raw bodies would call every
    repeat a new point, which is exactly the duplicate this is meant to catch.
    """
    body = text.split("\n---\n")[0]
    lines = [line for line in body.splitlines() if not line.strip().startswith("**")]
    return " ".join(" ".join(lines).split()).casefold()

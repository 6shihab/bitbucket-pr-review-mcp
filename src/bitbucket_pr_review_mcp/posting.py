"""Posting a Finding, and everything that has to be true first.

The order is the point. The Review Basis is re-checked against the Pull Request's current
head, then the Anchor is checked against the hunks of the diff at that Basis, and only
then does anything leave the process. A comment posted against a moved branch is not
wrong in a way anyone will notice — it is attached to real code, just not the code the
review was about — so the check has to happen before the write, not after it.

Afterwards, Bitbucket's own record of where the comment landed is read back off the
response. A comment that came back orphaned is reported rather than counted as success:
posting and landing are different events, and only one of them is worth reporting as
done.
"""

from __future__ import annotations

from dataclasses import dataclass

from .anchors import Anchor, anchor_in
from .client import BitbucketClient
from .comments import Comment, comments_path, read_comment
from .diffs import DiffCache, fetch_diff
from .findings import Finding, Reviewer
from .pullrequests import PullRequest, fetch_pull_request
from .references import PullRequestRef

# Git's own minimum abbreviation. Shorter than this is not a commit, it is a coincidence.
MIN_BASIS = 7


class BasisMoved(RuntimeError):
    """The branch moved since the diff was read. The message says to re-fetch."""


@dataclass(frozen=True, slots=True)
class Posted:
    """What actually happened to one Finding."""

    finding: Finding
    comment: Comment | None
    error: str | None = None

    @property
    def landed(self) -> bool:
        return self.comment is not None and not self.comment.is_orphaned

    def describe(self) -> str:
        where = self.finding.anchor.describe() if self.finding.anchor else "the pull request"
        if self.comment is None:
            return f"NOT POSTED — {where}: {self.error}"
        if self.comment.is_orphaned:
            return (
                f"POSTED BUT ORPHANED — comment #{self.comment.id} on {where}. Bitbucket "
                "accepted it and then could not place it against the current code. Say so "
                "rather than treating it as delivered."
            )
        return f"posted — comment #{self.comment.id} on {self.comment.anchor}"


def same_commit(one: str, other: str) -> bool:
    """Whether two spellings name the same commit.

    Bitbucket abbreviates: the pull request payload gives twelve characters while
    `/commits` gives forty, and comparing those with `==` would refuse every write. So the
    shorter has to be a prefix of the longer — and it has to be long enough to mean
    something, or a two-character "basis" would match half the repository.
    """
    left, right = one.strip().lower(), other.strip().lower()
    if len(left) < MIN_BASIS or len(right) < MIN_BASIS:
        return False

    shorter, longer = sorted((left, right), key=len)
    return longer.startswith(shorter)


def check_basis(claimed: str, pull_request: PullRequest) -> None:
    """Refuse the write if the branch moved under the review. Raises BasisMoved."""
    given = (claimed or "").strip()
    if len(given) < MIN_BASIS:
        raise BasisMoved(
            f"{claimed!r} is not a Review Basis. Pass the one bitbucket_get_pull_request "
            "returned, in full."
        )

    if not same_commit(given, pull_request.review_basis):
        raise BasisMoved(
            f"The Review Basis has moved: you read the diff at {given}, and this pull "
            f"request is now at {pull_request.review_basis}. The branch was pushed to "
            "while you were reviewing, so the lines you are commenting on may be "
            "different lines now. Nothing was posted. Re-read the pull request and its "
            "diff, then post against the new Basis."
        )


async def post_finding(
    client: BitbucketClient,
    ref: PullRequestRef,
    finding: Finding,
    basis: str,
    reviewer: Reviewer,
    cache: DiffCache | None = None,
) -> Posted:
    """Check the Basis, check the Anchor, post, and read back where it landed."""
    pull_request = await fetch_pull_request(client, ref)
    check_basis(basis, pull_request)

    payload: dict[str, object] = {"content": {"raw": finding.body(reviewer)}}
    if finding.anchor is not None:
        diff = await fetch_diff(client, ref, pull_request.review_basis, cache)
        payload["inline"] = anchor_in(diff, finding.anchor).inline()

    response = await client.request("POST", comments_path(ref), json=payload)
    return Posted(finding=finding, comment=read_comment(response.json(), our_account_id=None))


def anchor_of(path: str, line: int, side: str, through: int | None = None) -> Anchor:
    """Build an Anchor from tool arguments, refusing a nonsensical one immediately."""
    return Anchor(path=path.strip(), line=line, side=side.strip().lower(), through=through)

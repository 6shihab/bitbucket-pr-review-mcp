"""Commit history, for a ref or for a Pull Request — exactly one of the two.

"What was the last commit to this file trying to do" is a question a reviewer asks
constantly and a linter never does. Two endpoints answer it, and ADR-0005 puts them
behind one tool with a required either/or rather than two tools: a second tool would be
a coin-flip the model makes on every call, and it would guess wrong on the call where
the distinction mattered.

The either/or is enforced here rather than in the tool signature, because "exactly one
of these two optional arguments" is not a thing a schema can say.
"""

from __future__ import annotations

from dataclasses import dataclass

from .client import BitbucketClient
from .references import PullRequestRef, Repository
from .render import field_table, notice, untrusted

SUBJECT_LENGTH = 100


class AmbiguousRequest(ValueError):
    """Neither a ref nor a Pull Request, or both. The message says which to give."""


@dataclass(frozen=True, slots=True)
class Commit:
    """One commit, reduced to what a reviewer reads."""

    hash: str
    author: str
    date: str
    message: str

    @property
    def subject(self) -> str:
        first = self.message.strip().split("\n", 1)[0]
        return first if len(first) <= SUBJECT_LENGTH else f"{first[:SUBJECT_LENGTH - 1]}…"

    def row(self) -> str:
        return f"| `{self.hash[:12]}` | {self.date[:10]} | {self.author} | {self.subject} |"


@dataclass(frozen=True, slots=True)
class Commits:
    """A stretch of history, and what was asked for to get it."""

    subject: str
    commits: tuple[Commit, ...]
    total: int
    more_remain: bool

    @property
    def truncated(self) -> bool:
        return self.more_remain or len(self.commits) < self.total

    def to_markdown(self) -> str:
        lines = [
            f"# Commits in {self.subject}",
            "",
            field_table([("Commits shown", str(len(self.commits)))]),
            "",
        ]

        if self.truncated:
            lines += [
                notice(
                    f"Truncated: the {len(self.commits)} most recent are listed and there "
                    "are more. This is the top of the history, not all of it."
                ),
                "",
            ]

        table = "\n".join(
            [
                "| Commit | Date | Author | Subject |",
                "|---|---|---|---|",
                *(commit.row() for commit in self.commits),
            ]
        )
        return "\n".join([*lines, untrusted(table)])


async def fetch_commits(
    client: BitbucketClient,
    *,
    repository: Repository | None = None,
    ref: str | None = None,
    pull_request: PullRequestRef | None = None,
    limit: int = 50,
) -> Commits:
    """History for a ref or for a Pull Request. Exactly one, or an error saying so."""
    path, subject = _target(repository, ref, pull_request)
    values, more_remain = await client.get_pages(path, params={"pagelen": min(limit, 100)})
    commits = [read_commit(value) for value in values if isinstance(value, dict)]

    return Commits(
        subject=subject,
        commits=tuple(commits[:limit]),
        total=len(commits),
        more_remain=more_remain or len(commits) > limit,
    )


def _target(
    repository: Repository | None, ref: str | None, pull_request: PullRequestRef | None
) -> tuple[str, str]:
    named_ref = (ref or "").strip()
    if named_ref and pull_request is not None:
        raise AmbiguousRequest(
            "Give either a ref or a pull request, not both: they are different questions "
            "and answering the wrong one silently would be worse than this error."
        )
    if not named_ref and pull_request is None:
        raise AmbiguousRequest(
            "Give a ref (a branch or commit, with its repository) or a pull request. "
            "A ref answers 'what has been happening to this code'; a pull request "
            "answers 'what is in this change'."
        )

    if pull_request is not None:
        return (
            f"/2.0/repositories/{pull_request.workspace}/{pull_request.repo}"
            f"/pullrequests/{pull_request.pull_request_id}/commits",
            f"pull request {pull_request}",
        )

    if repository is None:
        raise AmbiguousRequest("A ref needs a repository: give 'workspace/repo' as well.")

    return (
        f"/2.0/repositories/{repository.workspace}/{repository.repo}/commits/{named_ref}",
        f"{repository} at `{named_ref}`",
    )


def read_commit(value: dict) -> Commit:
    """Bitbucket's commit shape. `author.user` is absent for unmatched email addresses."""
    author = value.get("author") or {}
    user = author.get("user") or {}
    return Commit(
        hash=str(value.get("hash") or ""),
        author=str(user.get("display_name") or author.get("raw") or "unknown"),
        date=str(value.get("date") or ""),
        message=str(value.get("message") or ""),
    )

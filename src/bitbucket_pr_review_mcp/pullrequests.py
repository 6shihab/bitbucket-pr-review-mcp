"""Reading a Pull Request.

Knows about Bitbucket's response shapes and about the domain; knows nothing about MCP.

The Review Basis — the source commit the Pull Request currently points at — is carried
out of every read deliberately. Everything this server later posts is validated against
it, so a read that dropped it would make the force-push guard unbuildable.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .client import BitbucketClient
from .references import PullRequestRef
from .render import field_table, untrusted

OPEN_STATE = "OPEN"


@dataclass(frozen=True, slots=True)
class PullRequest:
    """One Pull Request, as much of it as a Caller needs before reading any code."""

    ref: PullRequestRef
    title: str
    state: str
    author: str
    source_branch: str
    destination_branch: str
    description: str
    review_basis: str
    html_url: str

    @property
    def is_open(self) -> bool:
        return self.state.upper() == OPEN_STATE

    def to_markdown(self) -> str:
        heading = f"# Pull request {self.ref} — {self.state}"
        if not self.is_open:
            heading += (
                f"\n\n> This pull request is {self.state}. A review of it is follow-up "
                "rather than a gate — say so in anything you post."
            )

        facts = field_table(
            [
                ("State", self.state),
                ("Author", self.author),
                ("Source branch", f"`{self.source_branch}`"),
                ("Destination branch", f"`{self.destination_branch}`"),
                ("Review Basis", f"`{self.review_basis}`"),
                ("Link", self.html_url),
            ]
        )

        return (
            f"{heading}\n\n{facts}\n\n"
            f"## Title\n\n{untrusted(self.title)}\n\n"
            f"## Description\n\n{untrusted(self.description)}\n\n"
            "Every posted comment must carry this Review Basis. If the branch is "
            "force-pushed before you post, the write is refused and you re-read the diff."
        )


async def fetch_pull_request(client: BitbucketClient, ref: PullRequestRef) -> PullRequest:
    payload = await client.get_json(pull_request_path(ref))
    return read_pull_request(ref, payload)


def pull_request_path(ref: PullRequestRef) -> str:
    return f"/2.0/repositories/{ref.workspace}/{ref.repo}/pullrequests/{ref.pull_request_id}"


def read_pull_request(ref: PullRequestRef, payload: dict[str, Any]) -> PullRequest:
    """Read Bitbucket's pull request shape. Extra keys are ignored, missing ones defaulted."""
    source = payload.get("source") or {}
    destination = payload.get("destination") or {}

    return PullRequest(
        ref=ref,
        title=_text(payload.get("title")),
        state=_text(payload.get("state"), default="UNKNOWN"),
        author=_text((payload.get("author") or {}).get("display_name"), default="unknown"),
        source_branch=_text((source.get("branch") or {}).get("name"), default="unknown"),
        destination_branch=_text((destination.get("branch") or {}).get("name"), default="unknown"),
        description=_text(payload.get("description")),
        review_basis=_text((source.get("commit") or {}).get("hash")),
        html_url=_text(((payload.get("links") or {}).get("html") or {}).get("href")),
    )


def _text(value: Any, *, default: str = "") -> str:
    return value.strip() if isinstance(value, str) else default

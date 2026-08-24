"""Repository metadata: mostly here for the default branch.

A Caller that wants to read a file "as it is on main" has to know what main is called,
and a surprising number of repositories do not call it main. Everything else on this
response is cheap context — language, size, whether it is private — that helps a review
pitch itself, so it comes along rather than being fetched separately.
"""

from __future__ import annotations

from dataclasses import dataclass

from .client import BitbucketClient
from .references import Repository
from .render import field_table, untrusted

UNKNOWN_BRANCH = "unknown"


@dataclass(frozen=True, slots=True)
class RepositoryInfo:
    """One repository, as much of it as a review needs before reading code."""

    repository: Repository
    default_branch: str
    description: str
    language: str
    is_private: bool
    size_bytes: int
    html_url: str

    def to_markdown(self) -> str:
        facts = field_table(
            [
                ("Default branch", f"`{self.default_branch}`"),
                ("Language", self.language or "not stated"),
                ("Visibility", "private" if self.is_private else "public"),
                ("Size", f"{self.size_bytes:,} bytes"),
                ("Link", self.html_url or "—"),
            ]
        )
        return "\n".join(
            [
                f"# Repository {self.repository}",
                "",
                facts,
                "",
                "## Description",
                "",
                untrusted(self.description),
            ]
        )


async def fetch_repository(client: BitbucketClient, repository: Repository) -> RepositoryInfo:
    payload = await client.get_json(repository_path(repository))
    return read_repository(repository, payload)


def repository_path(repository: Repository) -> str:
    return f"/2.0/repositories/{repository.workspace}/{repository.repo}"


def read_repository(repository: Repository, payload: dict) -> RepositoryInfo:
    mainbranch = payload.get("mainbranch") or {}
    size = payload.get("size")

    return RepositoryInfo(
        repository=repository,
        default_branch=str(mainbranch.get("name") or UNKNOWN_BRANCH),
        description=str(payload.get("description") or ""),
        language=str(payload.get("language") or ""),
        is_private=bool(payload.get("is_private", True)),
        size_bytes=size if isinstance(size, int) else 0,
        html_url=str(((payload.get("links") or {}).get("html") or {}).get("href") or ""),
    )

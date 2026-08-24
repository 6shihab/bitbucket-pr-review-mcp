"""Reading the repository itself: one file, or one directory, at a named ref.

ADR-0006's widening lives here. A diff read in isolation produces the worst kind of
review — confident about syntax, blind to whether the changed function has three other
callers — so the Caller can read any file in an Allowlisted Repository, not only the
files the Pull Request touched.

Both live behind one Bitbucket endpoint: `/src/{ref}/{path}` returns the file when the
path is a file and a JSON listing when it is a directory. That ambiguity is resolved
here by the response's content type rather than by asking the Caller to know which it
had, because a Caller that guessed wrong should get the right answer, not an error.

Line numbers are added to file content because "the same pattern appears at line 88" is
most of what looking around is for. They are numbered from the file, not from the diff,
and the response says so: an Anchor still has to come from a hunk (ticket 06).
"""

from __future__ import annotations

from dataclasses import dataclass

from .changes import BINARY, classify
from .client import BitbucketClient
from .references import Repository
from .render import clip, field_table, notice, untrusted

DIRECTORY_TYPE = "commit_directory"

# Bitbucket answers /src with the file itself, or with JSON when the path is a directory.
JSON_TYPES = ("application/json", "application/hal+json")


class UnreadablePath(ValueError):
    """The path cannot be read as asked. The message says what to do instead."""


@dataclass(frozen=True, slots=True)
class SourceFile:
    """One file's contents, as of one ref."""

    repository: Repository
    ref: str
    path: str
    content: str
    is_binary: bool

    def to_markdown(self, *, limit: int) -> str:
        lines = [f"# `{self.path}` in {self.repository} at `{self.ref}`", ""]

        if self.is_binary:
            lines += [
                notice(
                    f"`{self.path}` is a binary file. Its bytes are not shown: there is "
                    "nothing here to read or to anchor a comment to."
                )
            ]
            return "\n".join(lines)

        body, cut = clip(_numbered(self.content), limit)
        if cut:
            lines += [
                notice(
                    f"Truncated at {limit} characters. Read a narrower path, or use "
                    "bitbucket_search_code to find the part of this file you need."
                ),
                "",
            ]
        lines += [
            "Line numbers are this file's own, at this ref. A comment's Anchor must "
            "still come from a diff hunk, not from these.",
            "",
            untrusted(body or "(empty file)"),
        ]
        return "\n".join(lines)


@dataclass(frozen=True, slots=True)
class DirectoryEntry:
    """One line of a directory listing."""

    path: str
    is_directory: bool
    size: int | None

    @property
    def name(self) -> str:
        return self.path.rstrip("/").rsplit("/", 1)[-1] + ("/" if self.is_directory else "")

    def row(self) -> str:
        kind = "dir" if self.is_directory else "file"
        size = "—" if self.size is None else f"{self.size:,}"
        return f"| `{self.name}` | {kind} | {size} |"


@dataclass(frozen=True, slots=True)
class Directory:
    """One directory's entries, as of one ref."""

    repository: Repository
    ref: str
    path: str
    entries: tuple[DirectoryEntry, ...]
    total: int
    more_remain: bool

    @property
    def truncated(self) -> bool:
        return self.more_remain or len(self.entries) < self.total

    def to_markdown(self) -> str:
        shown = f"`{self.path or '/'}` in {self.repository} at `{self.ref}`"
        lines = [
            f"# Directory {shown}",
            "",
            field_table([("Entries", str(self.total)), ("Ref", f"`{self.ref}`")]),
            "",
        ]

        if self.truncated:
            lines += [
                notice(
                    f"Truncated: {len(self.entries)} of {self.total} entries listed. Ask "
                    "for a subdirectory rather than expecting the rest here."
                ),
                "",
            ]

        table = "\n".join(
            [
                "| Entry | Kind | Bytes |",
                "|---|---|---|",
                *(entry.row() for entry in self.entries),
            ]
        )
        return "\n".join([*lines, untrusted(table)])


async def fetch_file(
    client: BitbucketClient, repository: Repository, path: str, ref: str
) -> SourceFile:
    """One file at one ref. Raises UnreadablePath when the path is a directory."""
    response = await client.request("GET", source_path(repository, ref, path), accept="*/*")

    if _is_json(response):
        raise UnreadablePath(
            f"{path!r} is a directory, not a file. Call bitbucket_get_directory for it."
        )

    declared_binary = BINARY in classify(path)
    body = response.text
    return SourceFile(
        repository=repository,
        ref=ref,
        path=_clean(path),
        content="" if declared_binary else body,
        is_binary=declared_binary or "\x00" in body[:4096],
    )


async def fetch_directory(
    client: BitbucketClient, repository: Repository, path: str, ref: str, limit: int
) -> Directory:
    """One directory's entries at one ref."""
    values, more_remain = await client.get_pages(
        source_path(repository, ref, path), params={"pagelen": 100}
    )
    entries = [read_entry(value) for value in values if isinstance(value, dict)]
    entries.sort(key=lambda entry: (not entry.is_directory, entry.path.lower()))

    return Directory(
        repository=repository,
        ref=ref,
        path=_clean(path),
        entries=tuple(entries[:limit]),
        total=len(entries),
        more_remain=more_remain,
    )


def source_path(repository: Repository, ref: str, path: str) -> str:
    cleaned = _clean(path)
    trailing = f"/{cleaned}" if cleaned else "/"
    return f"/2.0/repositories/{repository.workspace}/{repository.repo}/src/{_ref(ref)}{trailing}"


def _clean(path: str) -> str:
    """Refuse traversal here rather than relying on the guard to normalise it away.

    `assert_permitted` does resolve `..` before matching, so an escape would be refused
    anyway — but it would be refused as "outside the read surface", which tells a Caller
    nothing about the mistake it made. This says it plainly.
    """
    candidate = path.strip().strip("/")
    if any(segment == ".." for segment in candidate.split("/")):
        raise UnreadablePath(
            f"{path!r} climbs out of the repository. Give a path from the repository "
            "root, like 'src/app/retry.py'."
        )
    return "/".join(segment for segment in candidate.split("/") if segment not in ("", "."))


def _ref(ref: str) -> str:
    candidate = ref.strip()
    if not candidate:
        raise UnreadablePath(
            "No ref given. Read a file at a named commit or branch — the Review Basis "
            "from bitbucket_get_pull_request is usually the one you want."
        )
    if "/" in candidate and not candidate.startswith("refs/"):
        # A branch like 'feature/retry' is legal but would split the URL path; Bitbucket
        # accepts the commit hash for it, and a review should be pinned to one anyway.
        raise UnreadablePath(
            f"{ref!r} contains a slash, which this endpoint reads as a path separator. "
            "Use the commit hash — the Review Basis pins the review to one commit."
        )
    return candidate


def read_entry(value: dict) -> DirectoryEntry:
    size = value.get("size")
    return DirectoryEntry(
        path=str(value.get("path") or ""),
        is_directory=value.get("type") == DIRECTORY_TYPE,
        size=size if isinstance(size, int) else None,
    )


def _is_json(response) -> bool:
    return response.headers.get("content-type", "").split(";")[0].strip() in JSON_TYPES


def _numbered(content: str) -> str:
    lines = content.replace("\r\n", "\n").split("\n")
    width = len(str(len(lines)))
    return "\n".join(f"{index:>{width}} | {line}" for index, line in enumerate(lines, start=1))

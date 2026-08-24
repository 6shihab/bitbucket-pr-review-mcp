"""The diff: fetched once per Review Basis, parsed, and sliced locally.

Bitbucket's diff endpoint takes no `path` parameter — only `context` — so per-file
reading is served by fetching the whole diff once, keeping it against the Review Basis,
and cutting it up here. Browsing a forty-file Pull Request file by file therefore costs
one network fetch rather than forty (ADR-0005).

The Basis is the cache key, and that is the load-bearing part. A diff cached against a
Pull Request would still be served after a force-push, which is a diff of code that no
longer exists — the worst possible thing to review against, because it looks fine. Keyed
against the Basis, a force-push simply misses.

The parser is deliberately small. It splits the unified diff into files and hunks and
keeps every line it did not understand, so an unusual header is carried through to the
Caller rather than dropped by a parser that thought it knew better. Ticket 06 validates
Anchors against these hunks; that is why hunk ranges are read now rather than skipped.
"""

from __future__ import annotations

import re
from collections import OrderedDict
from dataclasses import dataclass

from .client import BitbucketClient
from .references import PullRequestRef
from .render import clip, field_table, notice, untrusted

FILE_HEADER = re.compile(r"^diff --git a/(?P<old>.+?) b/(?P<new>.+?)$")
HUNK_HEADER = re.compile(
    r"^@@ -(?P<old_start>\d+)(?:,(?P<old_count>\d+))? "
    r"\+(?P<new_start>\d+)(?:,(?P<new_count>\d+))? @@(?P<heading>.*)$"
)
OLD_PATH = re.compile(r"^--- (?:a/)?(?P<path>.+)$")
NEW_PATH = re.compile(r"^\+\+\+ (?:b/)?(?P<path>.+)$")

DEV_NULL = "/dev/null"

CACHE_CAPACITY = 4


@dataclass(frozen=True, slots=True)
class Hunk:
    """One `@@` block: where it sits in each side of the file, and its lines."""

    old_start: int
    old_count: int
    new_start: int
    new_count: int
    heading: str
    lines: tuple[str, ...]

    @property
    def header(self) -> str:
        return (
            f"@@ -{self.old_start},{self.old_count} "
            f"+{self.new_start},{self.new_count} @@{self.heading}"
        )

    def to_text(self) -> str:
        return "\n".join((self.header, *self.lines))


@dataclass(frozen=True, slots=True)
class FileDiff:
    """Everything the diff says about one file."""

    old_path: str | None
    new_path: str | None
    header: tuple[str, ...]
    hunks: tuple[Hunk, ...]
    is_binary: bool

    @property
    def path(self) -> str:
        """What to call this file: where it ended up, or where it was if it was deleted."""
        return self.new_path or self.old_path or "(unknown)"

    @property
    def is_rename(self) -> bool:
        return bool(self.old_path and self.new_path and self.old_path != self.new_path)

    def names(self) -> tuple[str, ...]:
        return tuple(name for name in (self.new_path, self.old_path) if name)

    def to_text(self) -> str:
        return "\n".join((*self.header, *(hunk.to_text() for hunk in self.hunks)))


@dataclass(frozen=True, slots=True)
class Diff:
    """One Pull Request's whole diff, at one Review Basis."""

    basis: str
    files: tuple[FileDiff, ...]

    def paths(self) -> tuple[str, ...]:
        return tuple(file.path for file in self.files)

    def for_path(self, path: str) -> FileDiff | None:
        """Find one file. Exact match first, then a unique suffix — a Caller reading a
        manifest row will give the full path, but one reading a hunk header may not."""
        wanted = path.strip().lstrip("./")
        if not wanted:
            return None

        for file in self.files:
            if wanted in file.names():
                return file

        suffix_matches = [
            file
            for file in self.files
            if any(name == wanted or name.endswith(f"/{wanted}") for name in file.names())
        ]
        return suffix_matches[0] if len(suffix_matches) == 1 else None

    def to_text(self) -> str:
        return "\n".join(file.to_text() for file in self.files)


def parse_diff(text: str, basis: str) -> Diff:
    """Split a unified diff into files and hunks, keeping what it does not recognise."""
    files: list[FileDiff] = []
    header: list[str] = []
    hunks: list[Hunk] = []
    current: Hunk | None = None
    lines: list[str] = []
    old_path: str | None = None
    new_path: str | None = None
    binary = False
    started = False

    def close_hunk() -> None:
        nonlocal current, lines
        if current is not None:
            hunks.append(_with_lines(current, lines))
        current, lines = None, []

    def close_file() -> None:
        nonlocal header, hunks, old_path, new_path, binary, started
        close_hunk()
        if started:
            files.append(
                FileDiff(
                    old_path=old_path,
                    new_path=new_path,
                    header=tuple(header),
                    hunks=tuple(hunks),
                    is_binary=binary,
                )
            )
        header, hunks, old_path, new_path, binary, started = [], [], None, None, False, False

    for line in text.replace("\r\n", "\n").split("\n"):
        file_header = FILE_HEADER.match(line)
        if file_header:
            close_file()
            started = True
            old_path = _real(file_header.group("old"))
            new_path = _real(file_header.group("new"))
            header.append(line)
            continue

        if not started:
            continue  # preamble before the first file header, if a diff ever has one

        hunk_header = HUNK_HEADER.match(line)
        if hunk_header:
            close_hunk()
            current = _empty_hunk(hunk_header)
            continue

        if current is not None:
            lines.append(line)
            continue

        if line.startswith("Binary files ") or line.startswith("GIT binary patch"):
            binary = True
        old = OLD_PATH.match(line)
        new = NEW_PATH.match(line)
        if old:
            old_path = _real(old.group("path"))
        elif new:
            new_path = _real(new.group("path"))
        header.append(line)

    close_file()
    return Diff(basis=basis, files=tuple(files))


class DiffCache:
    """The last few diffs, each remembered against the Basis it was read at.

    Small and in-process on purpose: it exists to stop file-by-file browsing from
    re-fetching, not to be a store. Nothing here survives a restart, and nothing should.
    """

    def __init__(self, capacity: int = CACHE_CAPACITY) -> None:
        self._capacity = capacity
        self._diffs: OrderedDict[tuple[str, str], Diff] = OrderedDict()

    def get(self, ref: PullRequestRef, basis: str) -> Diff | None:
        found = self._diffs.get(_key(ref, basis))
        if found is not None:
            self._diffs.move_to_end(_key(ref, basis))
        return found

    def put(self, ref: PullRequestRef, diff: Diff) -> None:
        self._diffs[_key(ref, diff.basis)] = diff
        self._diffs.move_to_end(_key(ref, diff.basis))
        while len(self._diffs) > self._capacity:
            self._diffs.popitem(last=False)

    def __len__(self) -> int:
        return len(self._diffs)


async def fetch_diff(
    client: BitbucketClient,
    ref: PullRequestRef,
    basis: str,
    cache: DiffCache | None = None,
) -> Diff:
    """The diff at this Review Basis, from the cache when it is there."""
    if cache is not None:
        remembered = cache.get(ref, basis)
        if remembered is not None:
            return remembered

    text = await client.get_text(diff_path(ref))
    diff = parse_diff(text, basis)
    if cache is not None:
        cache.put(ref, diff)
    return diff


def diff_path(ref: PullRequestRef) -> str:
    return (
        f"/2.0/repositories/{ref.workspace}/{ref.repo}"
        f"/pullrequests/{ref.pull_request_id}/diff"
    )


def _key(ref: PullRequestRef, basis: str) -> tuple[str, str]:
    return (str(ref), basis)


def _real(path: str) -> str | None:
    """`/dev/null` is git saying "there was no file on this side", not a path."""
    cleaned = path.strip()
    return None if cleaned == DEV_NULL else cleaned


def _empty_hunk(match: re.Match[str]) -> Hunk:
    return Hunk(
        old_start=int(match.group("old_start")),
        old_count=int(match.group("old_count") or 1),
        new_start=int(match.group("new_start")),
        new_count=int(match.group("new_count") or 1),
        heading=match.group("heading"),
        lines=(),
    )


def _with_lines(hunk: Hunk, lines: list[str]) -> Hunk:
    kept = list(lines)
    while kept and not kept[-1].strip():
        kept.pop()  # the blank line before the next file header is not part of this hunk
    return Hunk(
        old_start=hunk.old_start,
        old_count=hunk.old_count,
        new_start=hunk.new_start,
        new_count=hunk.new_count,
        heading=hunk.heading,
        lines=tuple(kept),
    )


class PathNotInDiff(LookupError):
    """The Caller named a file this Pull Request does not touch."""


def select(diff: Diff, path: str) -> FileDiff:
    """One file's hunks, or an error that says how to find the real paths."""
    found = diff.for_path(path)
    if found is not None:
        return found

    near = [candidate for candidate in diff.paths() if _stem(candidate) == _stem(path)]
    suggestion = f" Did you mean {', '.join(f'`{name}`' for name in near[:3])}?" if near else ""
    raise PathNotInDiff(
        f"This pull request does not change {path!r}.{suggestion} Call "
        "bitbucket_get_pull_request_changes for the list of changed files, and use a "
        "path exactly as it appears there."
    )


def diff_markdown(
    ref: PullRequestRef,
    diff: Diff,
    file: FileDiff | None,
    *,
    limit: int,
) -> str:
    """The diff a Caller reads: what it is, whether it is all of it, then the content."""
    subject = f"`{file.path}`" if file is not None else f"{len(diff.files)} changed files"
    body, cut = clip(file.to_text() if file is not None else diff.to_text(), limit)

    lines = [
        f"# Diff for {ref} — {subject}",
        "",
        field_table(
            [
                ("Review Basis", f"`{diff.basis}`"),
                ("Files in this response", "1" if file is not None else str(len(diff.files))),
            ]
        ),
        "",
    ]

    if file is not None and file.is_binary:
        lines += [notice(f"`{file.path}` is binary. There are no lines to anchor a comment to.")]
        lines += [""]

    if cut:
        lines += [notice(_truncation_advice(file, limit)), ""]

    lines += [untrusted(body or "(no textual changes)")]
    return "\n".join(lines)


def _truncation_advice(file: FileDiff | None, limit: int) -> str:
    if file is not None:
        return (
            f"Truncated at {limit} characters: this one file's diff is larger than the "
            "response limit. Read the file itself at the Review Basis with "
            "bitbucket_get_file, and anchor comments only to hunks you have actually seen."
        )
    return (
        f"Truncated at {limit} characters. Do not review the rest from memory — ask for "
        "one file at a time with bitbucket_get_pull_request_diff(pull_request, path=...), "
        "using bitbucket_get_pull_request_changes to choose which files matter."
    )


def _stem(path: str) -> str:
    return path.rsplit("/", 1)[-1].lower()

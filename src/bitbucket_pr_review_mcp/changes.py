"""The manifest: what a Pull Request changed, before any of it is read.

Discover then detail. A Caller that asks for the whole diff first spends most of its
context on files it was never going to comment on; a manifest costs a few hundred tokens
and tells it where to look.

The flags are the part that earns its keep. A lockfile diff is thousands of lines that
mean "the resolver ran", a generated file is the output of something that should be
reviewed instead, and a binary file has no lines to comment on at all. Left unflagged,
all three read as ordinary changed files and attract ordinary review comments — which is
how a reviewer ends up leaving a note about a checksum.

Classification is by path, because that is all the diffstat endpoint gives us, and it is
advisory: the flag says "probably not worth your attention", never "you may not read
this". Nothing here filters anything out.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePosixPath

from .client import BitbucketClient
from .references import PullRequestRef
from .render import field_table, notice, untrusted

BINARY = "binary"
GENERATED = "generated"
LOCKFILE = "lockfile"

BINARY_SUFFIXES = frozenset(
    {
        ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".ico", ".webp", ".tif", ".tiff",
        ".pdf", ".zip", ".gz", ".bz2", ".xz", ".7z", ".tar", ".jar", ".war",
        ".woff", ".woff2", ".ttf", ".otf", ".eot",
        ".mp3", ".mp4", ".mov", ".avi", ".wav", ".webm",
        ".so", ".dll", ".dylib", ".exe", ".bin", ".class", ".pyc", ".wasm",
        ".xlsx", ".docx", ".pptx", ".sqlite", ".db",
    }
)

LOCKFILE_NAMES = frozenset(
    {
        "uv.lock", "poetry.lock", "pdm.lock", "pipfile.lock", "cargo.lock",
        "package-lock.json", "npm-shrinkwrap.json", "yarn.lock", "pnpm-lock.yaml",
        "bun.lockb", "composer.lock", "gemfile.lock", "mix.lock", "go.sum",
        "packages.lock.json", "gradle.lockfile", "flake.lock",
    }
)

GENERATED_SUFFIXES = (
    ".min.js", ".min.css", ".map", ".pb.go", ".pb.cc", ".pb.h", ".g.dart",
    ".freezed.dart", ".generated.ts", ".gen.go", "_pb2.py", "_pb2_grpc.py",
    ".designer.cs", ".snap",
)

GENERATED_DIRECTORIES = frozenset(
    {
        "generated", "__generated__", "gen", "dist", "build",
        "node_modules", "vendor", "__snapshots__",
    }
)


@dataclass(frozen=True, slots=True)
class ChangedFile:
    """One row of the manifest."""

    path: str
    old_path: str | None
    status: str
    added: int
    removed: int
    flags: tuple[str, ...]

    @property
    def is_rename(self) -> bool:
        return bool(self.old_path and self.old_path != self.path)

    @property
    def worth_reading(self) -> bool:
        return not self.flags

    def row(self) -> str:
        name = f"`{self.path}`"
        if self.is_rename:
            name += f" (was `{self.old_path}`)"
        flags = ", ".join(self.flags) or "none"
        return f"| {name} | {self.status} | +{self.added} / -{self.removed} | {flags} |"


@dataclass(frozen=True, slots=True)
class Changes:
    """Every file a Pull Request touches, at one Review Basis."""

    ref: PullRequestRef
    basis: str
    files: tuple[ChangedFile, ...]
    total: int
    more_remain: bool

    @property
    def truncated(self) -> bool:
        return self.more_remain or len(self.files) < self.total

    def to_markdown(self) -> str:
        header = [
            f"# Changed files in {self.ref}",
            "",
            field_table(
                [
                    ("Review Basis", f"`{self.basis}`"),
                    ("Files changed", str(self.total)),
                    ("Added / removed", f"+{self._added()} / -{self._removed()}"),
                    ("Flagged", str(sum(1 for file in self.files if file.flags))),
                ]
            ),
            "",
        ]

        if self.truncated:
            header.append(
                notice(
                    f"Truncated: {len(self.files)} of {self.total} files are listed"
                    f"{' and more pages remain' if self.more_remain else ''}. Review the "
                    "listed files first and read the rest of the diff a file at a time "
                    "with bitbucket_get_pull_request_diff — do not assume the unlisted "
                    "files are unchanged."
                )
            )
            header.append("")

        table = "\n".join(
            [
                "| File | Change | Lines | Flags |",
                "|---|---|---|---|",
                *(file.row() for file in self.files),
            ]
        )

        guidance = (
            "Flagged files are rarely worth a comment: a lockfile diff means the "
            "resolver ran, a generated file should be reviewed at its source, and a "
            "binary file has no lines to anchor to. Read the rest with "
            "bitbucket_get_pull_request_diff(pull_request, path=...)."
        )

        return "\n".join([*header, untrusted(table), "", guidance])

    def _added(self) -> int:
        return sum(file.added for file in self.files)

    def _removed(self) -> int:
        return sum(file.removed for file in self.files)


async def fetch_changes(
    client: BitbucketClient,
    ref: PullRequestRef,
    basis: str,
    limit: int,
) -> Changes:
    """The diffstat manifest, straight from Bitbucket's own endpoint."""
    values, more_remain = await client.get_pages(changes_path(ref), params={"pagelen": 100})
    files = [read_changed_file(entry) for entry in values if isinstance(entry, dict)]

    return Changes(
        ref=ref,
        basis=basis,
        files=tuple(files[:limit]),
        total=len(files),
        more_remain=more_remain,
    )


def changes_path(ref: PullRequestRef) -> str:
    return (
        f"/2.0/repositories/{ref.workspace}/{ref.repo}"
        f"/pullrequests/{ref.pull_request_id}/diffstat"
    )


def classify(path: str) -> tuple[str, ...]:
    """Flag a path as binary, generated or a lockfile. Advisory, never a filter."""
    name = PurePosixPath(path).name.lower()
    lowered = path.lower()
    flags: list[str] = []

    if PurePosixPath(lowered).suffix in BINARY_SUFFIXES:
        flags.append(BINARY)
    if name in LOCKFILE_NAMES:
        flags.append(LOCKFILE)
    if lowered.endswith(GENERATED_SUFFIXES) or _under_generated_directory(lowered):
        flags.append(GENERATED)

    return tuple(flags)


def _under_generated_directory(lowered: str) -> bool:
    return any(part in GENERATED_DIRECTORIES for part in PurePosixPath(lowered).parts[:-1])


def read_changed_file(entry: dict) -> ChangedFile:
    """One diffstat entry. Bitbucket sends no binary flag, hence `classify`."""
    old = (entry.get("old") or {}).get("path")
    new = (entry.get("new") or {}).get("path")
    path = new or old or "(unknown)"

    return ChangedFile(
        path=path,
        old_path=old,
        status=str(entry.get("status") or "modified"),
        added=_count(entry.get("lines_added")),
        removed=_count(entry.get("lines_removed")),
        flags=classify(path),
    )


def _count(value: object) -> int:
    return value if isinstance(value, int) and value >= 0 else 0

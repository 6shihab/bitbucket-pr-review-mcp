"""Where a comment attaches, and the translation that is easy to get backwards.

A Caller names an Anchor the way a human reads a diff: a path, a line, and which side of
the diff that line is on — `added`, `removed` or `context`. Bitbucket wants `inline.to`
for a line in the new file and `inline.from` for a line in the old one, and those two
fields are the single easiest thing on this API to invert. Inverting them does not raise
an error: it attaches the comment to a different line of real code, which is worse than
failing, because somebody will act on it.

So the translation happens in exactly one place, here, and every Anchor is checked
against the parsed hunks before anything is sent. A line that is not in the diff is
refused with the nearest lines that *are*, including their sides and their text, so the
Caller's next call is right rather than another guess.

A range anchors at its first line and names the whole block in the comment body: the API
has no way to express "these five lines", and pretending otherwise by silently commenting
on one of them would misrepresent what the Finding is about.
"""

from __future__ import annotations

from dataclasses import dataclass

from .diffs import (
    ADDED,
    CONTEXT,
    REMOVED,
    SIDES,
    Diff,
    DiffLine,
    FileDiff,
    select,
    walk,
)

__all__ = [
    "ADDED",
    "CONTEXT",
    "REMOVED",
    "SIDES",
    "Anchor",
    "AnchorNotInDiff",
    "Anchored",
    "anchor_in",
    "anchorable",
]

# How many nearby lines to offer back when an Anchor misses.
NEARBY = 6


class AnchorNotInDiff(ValueError):
    """The Anchor names a line this diff does not contain. The message names real ones."""


@dataclass(frozen=True, slots=True)
class Anchor:
    """A place in the diff, named the way a human reads one."""

    path: str
    line: int
    side: str
    through: int | None = None

    def __post_init__(self) -> None:
        if self.side not in SIDES:
            raise AnchorNotInDiff(
                f"{self.side!r} is not a side. Use one of: {', '.join(SIDES)} — 'added' "
                "for a line the change introduces, 'removed' for one it deletes, "
                "'context' for an unchanged line shown around them."
            )
        if self.line < 1:
            raise AnchorNotInDiff(f"Line {self.line} is not a line number.")
        if self.through is not None and self.through < self.line:
            raise AnchorNotInDiff(
                f"The range {self.line}-{self.through} ends before it starts."
            )

    @property
    def lines(self) -> range:
        return range(self.line, (self.through or self.line) + 1)

    @property
    def is_range(self) -> bool:
        return self.through is not None and self.through > self.line

    def describe(self) -> str:
        where = f"{self.line}-{self.through}" if self.is_range else str(self.line)
        return f"{self.path} {self.side} line{'s' if self.is_range else ''} {where}"


@dataclass(frozen=True, slots=True)
class Anchored:
    """An Anchor that has been checked against the diff, and where Bitbucket wants it."""

    anchor: Anchor
    file: FileDiff

    def inline(self) -> dict[str, object]:
        """Bitbucket's inline object. `to` is the new file, `from` is the old one.

        The comment lands on the first line of a range: the API cannot express a block,
        and the body names the range so the Finding still says what it is about.
        """
        if self.anchor.side == REMOVED:
            return {"path": self.file.path, "from": self.anchor.line}
        return {"path": self.file.path, "to": self.anchor.line}


def anchor_in(diff: Diff, anchor: Anchor) -> Anchored:
    """Check an Anchor against the diff, or refuse it with lines that would have worked."""
    file = select(diff, anchor.path)  # raises PathNotInDiff, which already teaches
    lines = anchorable(file)

    wanted = {(anchor.side, number) for number in anchor.lines}
    present = {(line.side, line.number) for line in lines}
    missing = sorted(number for side, number in wanted - present if side == anchor.side)

    if missing:
        raise AnchorNotInDiff(_refusal(anchor, missing, lines))
    return Anchored(anchor=anchor, file=file)


def anchorable(file: FileDiff) -> tuple[DiffLine, ...]:
    """Every line of this file's diff that a comment can be attached to."""
    found: list[DiffLine] = []
    for hunk in file.hunks:
        found.extend(walk(hunk))
    return tuple(found)


def _refusal(anchor: Anchor, missing: list[int], lines: tuple[DiffLine, ...]) -> str:
    same_side = [line for line in lines if line.side == anchor.side]
    if not same_side:
        sides = sorted({line.side for line in lines})
        return (
            f"{anchor.path} has no {anchor.side} lines in this diff"
            + (f" — it has {', '.join(sides)} lines." if sides else " at all.")
            + " Read the diff again with bitbucket_get_pull_request_diff and anchor to a "
            "line it actually shows."
        )

    nearest = sorted(same_side, key=lambda line: abs(line.number - anchor.line))[:NEARBY]
    listed = "\n".join(f"  {line.describe()}" for line in sorted(nearest, key=lambda x: x.number))
    absent = ", ".join(str(number) for number in missing)

    return (
        f"{anchor.path} has no {anchor.side} line {absent} in this diff. The nearest "
        f"{anchor.side} lines that do exist are:\n{listed}\n"
        "Anchor to one of those, or re-read the diff — a line number that is not in a "
        "hunk would attach the comment to code nobody is reviewing."
    )

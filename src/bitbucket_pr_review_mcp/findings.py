"""A Finding, and the comment body it becomes.

The Caller forms the Finding; this server renders and carries it. That boundary is the
whole of ADR-0001, and it is why there is a Severity ladder here but no opinion about
which rung anything belongs on.

The Attribution Footer is not optional and there is no argument that removes it. A human
reading a pull request has to be able to tell machine-generated review from a colleague's
— and the Reviewer whose account it posts under is accountable for it, so their name goes
on it. A message that includes its own fake footer gets the real one anyway, appended
after it.
"""

from __future__ import annotations

from dataclasses import dataclass

from .anchors import Anchor

CRITICAL, HIGH, MEDIUM, LOW = "CRITICAL", "HIGH", "MEDIUM", "LOW"
SEVERITIES = (CRITICAL, HIGH, MEDIUM, LOW)

# The house ladder, not a second vocabulary invented here.
MEANING = {
    CRITICAL: "block",
    HIGH: "warn",
    MEDIUM: "info",
    LOW: "note",
}

MAX_CATEGORY = 40
MAX_MESSAGE = 4_000

TOOL_NAME = "bitbucket-pr-review-mcp"


class MalformedFinding(ValueError):
    """The Finding cannot be posted as given. The message says what to change."""


@dataclass(frozen=True, slots=True)
class Reviewer:
    """The human whose account this posts under, and who is accountable for it."""

    display_name: str
    email: str

    def named(self) -> str:
        if self.display_name and self.email:
            return f"{self.display_name} ({self.email})"
        return self.display_name or self.email or "an unidentified account"


@dataclass(frozen=True, slots=True)
class Finding:
    """One thing the Caller wants to say about one place in the code."""

    severity: str
    category: str
    message: str
    anchor: Anchor | None = None

    @classmethod
    def of(
        cls,
        *,
        severity: str,
        category: str,
        message: str,
        anchor: Anchor | None = None,
    ) -> Finding:
        graded = (severity or "").strip().upper()
        if graded not in SEVERITIES:
            raise MalformedFinding(
                f"{severity!r} is not a severity. Use one of: "
                + ", ".join(f"{name} ({MEANING[name]})" for name in SEVERITIES)
                + "."
            )

        said = (message or "").strip()
        if not said:
            raise MalformedFinding("A finding with no message says nothing. Write the point.")
        if len(said) > MAX_MESSAGE:
            raise MalformedFinding(
                f"This message is {len(said)} characters. Keep a comment under "
                f"{MAX_MESSAGE}: a review nobody reads is not a review."
            )

        named = (category or "").strip() or "review"
        if len(named) > MAX_CATEGORY:
            raise MalformedFinding(
                f"The category {named!r} is too long. Keep it to a couple of words, like "
                "'correctness' or 'error handling'."
            )

        return cls(severity=graded, category=named, message=said, anchor=anchor)

    @property
    def is_inline(self) -> bool:
        return self.anchor is not None

    def heading(self) -> str:
        head = f"**{self.severity} ({MEANING[self.severity]}) · {self.category}**"
        if self.anchor and self.anchor.is_range:
            head += f" — {self.anchor.side} lines {self.anchor.line}–{self.anchor.through}"
        return head

    def body(self, reviewer: Reviewer) -> str:
        """The comment as it will appear, footer included. There is no version without."""
        return f"{self.heading()}\n\n{self.message}\n\n{footer(reviewer)}"


def footer(reviewer: Reviewer) -> str:
    """The Attribution Footer. Every posted comment carries it; nothing suppresses it."""
    return (
        "---\n"
        f"🤖 Machine-generated review comment, posted by {reviewer.named()} "
        f"using `{TOOL_NAME}`. The model wrote it; the account holder is accountable "
        "for it. Reply here if it is wrong."
    )

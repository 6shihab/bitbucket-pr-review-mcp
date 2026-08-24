"""Naming a Repository and a Pull Request.

The bottom of the import spine: this module imports nothing from the project and
nothing that talks to a network. A Caller names a Pull Request with the URL a human
pasted or with a compact shorthand, and both land here to become the same value.

Parsing lives in one place on purpose. A workspace/repo/id triple in the tool signature
would relocate this work into the model, where it fails silently and inventively — and
it is this module's output that the Allowlisted Repository check runs against, so there
is exactly one place where "which repository is this really" gets decided.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlsplit

BITBUCKET_HOST = "bitbucket.org"

ACCEPTED_FORMS = (
    "Give either a Bitbucket URL "
    "('https://bitbucket.org/<workspace>/<repo>/pull-requests/<id>') "
    "or the shorthand 'workspace/repo/id'."
)

# Bitbucket slugs are lowercase alphanumerics with dashes, underscores and dots. Kept
# deliberately tight: a slug is about to be interpolated into a request path.
_SLUG = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


class InvalidReference(ValueError):
    """Raised when a reference cannot be read. The message names the accepted forms."""


@dataclass(frozen=True, slots=True)
class Repository:
    """One Bitbucket repository, named the way the allowlist names it."""

    workspace: str
    repo: str

    @property
    def full_name(self) -> str:
        return f"{self.workspace}/{self.repo}"

    def __str__(self) -> str:
        return self.full_name

    @classmethod
    def parse(cls, text: str) -> Repository:
        """Read 'workspace/repo' — the form the allowlist is written in."""
        candidate = text.strip()
        if not candidate:
            raise InvalidReference(f"No repository given. {ACCEPTED_FORMS}")

        segments = candidate.split("/")
        if len(segments) != 2:
            raise InvalidReference(f"Could not read a repository from {text!r}. {ACCEPTED_FORMS}")

        return cls(_slug(segments[0], "workspace", text), _slug(segments[1], "repository", text))


@dataclass(frozen=True, slots=True)
class PullRequestRef:
    """One Pull Request, however the Caller happened to name it."""

    workspace: str
    repo: str
    pull_request_id: int

    @property
    def repository(self) -> Repository:
        return Repository(self.workspace, self.repo)

    def __str__(self) -> str:
        return f"{self.workspace}/{self.repo}/{self.pull_request_id}"

    @classmethod
    def parse(cls, text: str) -> PullRequestRef:
        candidate = text.strip()
        if not candidate:
            raise InvalidReference(f"No pull request given. {ACCEPTED_FORMS}")

        segments = _url_segments(candidate) if _looks_like_url(candidate) else candidate.split("/")
        if len(segments) < 3:
            raise InvalidReference(f"Could not read a pull request from {text!r}. {ACCEPTED_FORMS}")

        workspace, repo, raw_id = segments[0], segments[1], segments[2]
        return cls(
            workspace=_slug(workspace, "workspace", text),
            repo=_slug(repo, "repository", text),
            pull_request_id=_pull_request_id(raw_id, text),
        )


def _looks_like_url(text: str) -> bool:
    return "://" in text or text.lower().startswith(
        (f"{BITBUCKET_HOST}/", f"www.{BITBUCKET_HOST}/")
    )


def _url_segments(text: str) -> list[str]:
    """Reduce a Pull Request URL to workspace, repo and id.

    Trailing segments are dropped rather than refused: Bitbucket appends /diff and
    /commits as you click through the tabs, and a human pasting from the address bar
    brings them along.
    """
    segments = _path_segments(text)
    try:
        marker = segments.index("pull-requests")
    except ValueError:
        raise InvalidReference(
            f"{text!r} is not a pull request URL — no 'pull-requests' segment. {ACCEPTED_FORMS}"
        ) from None

    if marker < 2 or marker + 1 >= len(segments):
        raise InvalidReference(f"Could not read a pull request from {text!r}. {ACCEPTED_FORMS}")

    return [segments[marker - 2], segments[marker - 1], segments[marker + 1]]


def _path_segments(text: str) -> list[str]:
    """Split a URL's path, having checked it is a Bitbucket URL at all."""
    parsed = urlsplit(text if "://" in text else f"https://{text}")
    host = parsed.netloc.lower().removeprefix("www.").split(":")[0]
    if host != BITBUCKET_HOST:
        raise InvalidReference(
            f"{text!r} is not on {BITBUCKET_HOST}. This server speaks only to Bitbucket Cloud."
        )
    return [segment for segment in parsed.path.split("/") if segment]


def _slug(value: str, what: str, original: str) -> str:
    if not _SLUG.match(value):
        raise InvalidReference(
            f"{value!r} is not a usable {what} in {original!r}. {ACCEPTED_FORMS}"
        )
    return value


def _pull_request_id(value: str, original: str) -> int:
    if not value.isdigit():
        raise InvalidReference(
            f"{value!r} is not a pull request number in {original!r}. {ACCEPTED_FORMS}"
        )
    number = int(value)
    if number < 1:
        raise InvalidReference(f"Pull request numbers start at 1, got {number} in {original!r}.")
    return number

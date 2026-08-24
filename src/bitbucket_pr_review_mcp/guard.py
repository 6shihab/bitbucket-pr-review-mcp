"""The chokepoint: which requests are allowed to leave this process.

ADR-0002, mechanism 2. Read that ADR before changing anything here, and read this
paragraph if you don't: Bitbucket's `write:pullrequest:bitbucket` scope grants comment,
approve, decline **and merge** as one indivisible unit, so the credential this server
holds is always capable of merging. Nothing about tokens or permissions prevents it.
What prevents it is that no tool asks for it (mechanism 1) and that this function
refuses it (mechanism 2) — and mechanism 2 is the one that still holds when somebody
adds a tool without reading mechanism 1.

The rule table is deliberately tiny and deliberately positive: everything not named
here is refused. Widening it is a security change, not a feature, and it belongs in a
ticket with its own tests.
"""

from __future__ import annotations

import posixpath
import re
from urllib.parse import urlsplit

from .references import Repository
from .settings import Allowlist

API_HOST = "api.bitbucket.org"

# GET /2.0/user — needed to verify a credential and to learn whose comments are ours.
_CURRENT_USER = re.compile(r"^/2\.0/user$")

# GET /2.0/repositories/{workspace}/{repo}/... — anything under an allowed repository.
_REPOSITORY_SCOPED = re.compile(r"^/2\.0/repositories/(?P<workspace>[^/]+)/(?P<repo>[^/]+)(/.*)?$")

# POST — the comments collection of one pull request, and nothing else.
_COMMENTS_COLLECTION = re.compile(
    r"^/2\.0/repositories/(?P<workspace>[^/]+)/(?P<repo>[^/]+)/pullrequests/\d+/comments$"
)

# PUT — one existing comment. This exists solely to keep a single canonical Summary
# Comment current instead of stacking a new one on every review.
_ONE_COMMENT = re.compile(
    r"^/2\.0/repositories/(?P<workspace>[^/]+)/(?P<repo>[^/]+)/pullrequests/\d+/comments/\d+$"
)


class Forbidden(PermissionError):
    """Raised when a request is refused before it is sent."""


def assert_permitted(method: str, url: str, allowlist: Allowlist) -> None:
    """Refuse anything outside the rule table. Raises Forbidden; returns None otherwise."""
    verb = method.upper()
    path = _normalise(url)

    if verb == "DELETE":
        raise Forbidden(
            "DELETE is refused everywhere. Nothing in this server deletes a comment: a "
            "stale comment may already carry a colleague's reply, and destroying a "
            "conversation to tidy a list is the worse outcome (ADR-0002)."
        )

    if verb == "GET":
        if _CURRENT_USER.match(path):
            return
        _require_allowlisted(_REPOSITORY_SCOPED, path, allowlist, verb)
        return

    if verb == "POST":
        _require_allowlisted(_COMMENTS_COLLECTION, path, allowlist, verb)
        return

    if verb == "PUT":
        _require_allowlisted(_ONE_COMMENT, path, allowlist, verb)
        return

    raise Forbidden(f"{verb} is not a method this server issues.")


def _require_allowlisted(
    pattern: re.Pattern[str], path: str, allowlist: Allowlist, verb: str
) -> None:
    match = pattern.match(path)
    if not match:
        raise Forbidden(_refusal(verb, path))

    repository = Repository(match.group("workspace"), match.group("repo"))
    if not allowlist.permits(repository):
        raise Forbidden(
            f"{repository.full_name} is not an allowlisted repository. "
            f"This server may touch: {', '.join(allowlist.names())}."
        )


def _refusal(verb: str, path: str) -> str:
    if verb == "GET":
        return f"GET {path} is outside this server's read surface."
    return (
        f"{verb} {path} is not a permitted write. This server may only POST a comment to "
        "a pull request's comments collection and PUT an existing comment. It cannot "
        "approve, decline, merge, or write to a repository (ADR-0002)."
    )


def _normalise(url: str) -> str:
    """Reduce a URL to the path the rules match against.

    A chokepoint that can be walked around by spelling the path differently is
    decoration. Query and fragment are discarded so neither can carry a permitted-looking
    suffix; `.` and `..` segments are resolved so a traversal cannot reach a refused
    endpoint through a permitted-looking prefix; repeated slashes collapse; a trailing
    slash is dropped. Matching is then exact and case-sensitive, so `/MERGE` does not
    match `/comments` and does not match anything else either.
    """
    split = urlsplit(url)
    if split.netloc and split.netloc.split(":")[0].lower() != API_HOST:
        raise Forbidden(
            f"{split.netloc} is not {API_HOST}. This server speaks to Bitbucket Cloud only."
        )

    path = posixpath.normpath(re.sub(r"/{2,}", "/", split.path or "/"))
    return path if path == "/" else path.rstrip("/")

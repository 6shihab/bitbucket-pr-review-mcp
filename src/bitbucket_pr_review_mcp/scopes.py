"""What the credential is allowed to do, read back from Bitbucket rather than assumed.

Bitbucket echoes the granted scopes on every API response in `x-oauth-scopes`, which is
the only way to learn what a pasted token can actually do — the token itself is opaque
and the Reviewer's memory of which checkboxes they ticked is not evidence.

The policy is narrow on purpose. A token that can write to a repository, administer one,
or drive pipelines is a token whose blast radius is nothing like "leaves comments on
pull requests", and ADR-0002 spends three mechanisms containing exactly that. Refusing
it at entry costs the Reviewer one minute; discovering it after an incident costs rather
more. Anything unrecognised is treated as excessive, so a scope Atlassian adds next year
fails closed.

Note the one thing this cannot do: `write:pullrequest:bitbucket` covers comment, approve,
decline **and** merge as an indivisible unit. This module cannot narrow that, and no
wording here should suggest otherwise — see ADR-0002.
"""

from __future__ import annotations

from dataclasses import dataclass

SCOPE_HEADER = "x-oauth-scopes"

TOKEN_PAGE = "https://id.atlassian.com/manage-profile/security/api-tokens"

# What this server needs. Either spelling of each, since Bitbucket accepts both the
# granular Atlassian scopes and the older app-password names on existing credentials.
#
# The user scope is easy to think optional and is not: `GET /2.0/user` is how setup shows
# the Reviewer whose account they just connected, and how the server later recognises its
# own comments — without it a re-review stacks duplicates instead of updating (ticket 05).
# Bitbucket refuses that endpoint with a 403 rather than an empty answer, so a token
# without it fails at the first tool call rather than degrading.
USER_READ = frozenset({"read:user:bitbucket", "account"})
REPOSITORY_READ = frozenset({"read:repository:bitbucket", "repository"})
PULL_REQUEST_WRITE = frozenset({"write:pullrequest:bitbucket", "pullrequest:write"})

REQUIRED = (
    "read:user:bitbucket",
    "read:repository:bitbucket",
    "write:pullrequest:bitbucket",
)

# Read-only scopes we tolerate alongside the required pair: they widen what can be read,
# never what can be changed, and Atlassian grants some of them implicitly.
_LEGACY_READ_ONLY = frozenset(
    {"account", "email", "issue", "project", "pullrequest", "repository", "snippet", "team", "wiki"}
)


@dataclass(frozen=True, slots=True)
class ScopeVerdict:
    """What Bitbucket says this credential may do, judged against what we need."""

    granted: tuple[str, ...]
    excessive: tuple[str, ...]
    missing: tuple[str, ...]
    verified: bool

    @property
    def acceptable(self) -> bool:
        """True when nothing beyond reading a repository and writing to pull requests."""
        return not self.excessive

    @property
    def complete(self) -> bool:
        return not self.missing

    def refusal(self) -> str:
        return (
            "This token grants more than this server is willing to hold: "
            f"{', '.join(self.excessive)}. It reads repositories and writes to pull "
            "requests, and a token that can also write to, administer, or run anything "
            "else turns a commenting tool into a much larger blast radius (ADR-0002). "
            f"Create a token at {TOKEN_PAGE} with only {' and '.join(REQUIRED)}."
        )

    def shortfall(self) -> str:
        return (
            f"This token is missing {', '.join(self.missing)}. Reviews will fail part-way "
            f"through. Create a token at {TOKEN_PAGE} granting {' and '.join(REQUIRED)}."
        )


def review_scopes(header: str | None) -> ScopeVerdict:
    """Judge the `x-oauth-scopes` header. An absent header verifies nothing."""
    if header is None:
        return ScopeVerdict(granted=(), excessive=(), missing=(), verified=False)

    granted = tuple(sorted({part.strip().lower() for part in header.split(",") if part.strip()}))
    excessive = tuple(scope for scope in granted if not _is_permitted(scope))

    missing = tuple(
        name
        for name, alternatives in zip(
            REQUIRED, (USER_READ, REPOSITORY_READ, PULL_REQUEST_WRITE), strict=True
        )
        if not (alternatives & set(granted))
    )
    return ScopeVerdict(granted=granted, excessive=excessive, missing=missing, verified=True)


def _is_permitted(scope: str) -> bool:
    if scope in PULL_REQUEST_WRITE:
        return True
    return scope.startswith("read:") or scope in _LEGACY_READ_ONLY

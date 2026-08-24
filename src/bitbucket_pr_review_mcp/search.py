"""Code search, and the two guards it needs that nothing else does.

Bitbucket has no repository-scoped code search. `GET /2.0/workspaces/{ws}/search/code`
searches an entire workspace, and its repository filter is the substring `repo:name`
*inside a caller-supplied query string*. Enforcing an allowlist by composing that string
would be enforcement by concatenation — defeated by a query that adds, removes or
duplicates a `repo:` term (ADR-0006).

So: the repository is a separate argument, the filter is composed here, a query that
tries to carry its own scoping is refused, and — the part that actually matters — every
result is checked against the allowlist before it is returned. The first three keep an
honest Caller in bounds. The last one is what holds when the query has been written by
something in a pull request description.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlsplit

from .client import BitbucketClient
from .guard import Forbidden
from .references import Repository
from .render import field_table, notice, untrusted
from .settings import Allowlist

# Search modifiers that change *what is searched* rather than what is matched. A Caller
# has no business setting these: the repository argument is how scope is chosen here.
SCOPING_MODIFIERS = ("repo:", "project:", "workspace:", "org:", "user:")

_MODIFIER = re.compile(r"(?:^|\s)(" + "|".join(SCOPING_MODIFIERS) + r")", re.IGNORECASE)

_ORIGIN = re.compile(r"^/2\.0/repositories/(?P<workspace>[^/]+)/(?P<repo>[^/]+)/")


class UnsafeQuery(ValueError):
    """The query tried to choose its own scope. The message says which argument does."""


@dataclass(frozen=True, slots=True)
class Match:
    """One matching line, with where it is."""

    path: str
    line: int
    text: str

    def row(self) -> str:
        return f"| `{self.path}` | {self.line or '—'} | `{self.text.strip()[:160]}` |"


@dataclass(frozen=True, slots=True)
class SearchResults:
    """What a search found inside one Allowlisted Repository."""

    repository: Repository
    query: str
    matches: tuple[Match, ...]
    total: int
    discarded: int

    @property
    def truncated(self) -> bool:
        return len(self.matches) < self.total

    def to_markdown(self) -> str:
        lines = [
            f"# Code search in {self.repository}",
            "",
            field_table(
                [
                    ("Query", f"`{self.query}`"),
                    ("Matches shown", f"{len(self.matches)} of {self.total}"),
                ]
            ),
            "",
        ]

        if self.truncated:
            lines += [
                notice(
                    f"Truncated: {len(self.matches)} of {self.total} matches are listed. "
                    "Narrow the query rather than reading the rest — search is for "
                    "finding a place to look, not for reading the repository."
                ),
                "",
            ]

        if self.discarded:
            lines += [
                notice(
                    f"{self.discarded} result(s) came back from outside "
                    f"{self.repository} and were discarded before you saw them."
                ),
                "",
            ]

        if not self.matches:
            return "\n".join([*lines, "No matches."])

        table = "\n".join(
            [
                "| File | Line | Match |",
                "|---|---|---|",
                *(match.row() for match in self.matches),
            ]
        )
        return "\n".join(
            [
                *lines,
                untrusted(table),
                "",
                "Read the surrounding code with bitbucket_get_file before drawing a "
                "conclusion from a matching line.",
            ]
        )


async def search_code(
    client: BitbucketClient,
    repository: Repository,
    query: str,
    allowlist: Allowlist,
    limit: int,
) -> SearchResults:
    """Search one Allowlisted Repository. The filter is ours; the results are checked."""
    # The chokepoint cannot make this check for us. Every other endpoint carries its
    # repository in the path, so `assert_permitted` refuses an unlisted one before the
    # request is built; here the repository lives in a query parameter, and the guard can
    # only see the workspace. So the allowlist is applied here, by hand, on the way out —
    # and again to the results on the way back.
    if not allowlist.permits(repository):
        raise Forbidden(
            f"{repository} is not an allowlisted repository. This server may search: "
            f"{', '.join(allowlist.names())}."
        )

    cleaned = _clean(query)
    payload = await client.get_json(
        search_path(repository.workspace),
        params={
            "search_query": f"repo:{repository.repo} {cleaned}",
            "pagelen": min(max(limit, 1), 100),
        },
    )

    kept: list[Match] = []
    discarded = 0
    for value in payload.get("values") or []:
        if not isinstance(value, dict):
            continue
        found = repository_of(value)
        if found is None or not allowlist.permits(found) or found != repository:
            # The request could not be narrower than a workspace, so the answer is.
            discarded += 1
            continue
        kept.extend(_matches(value))

    return SearchResults(
        repository=repository,
        query=cleaned,
        matches=tuple(kept[:limit]),
        total=len(kept),
        discarded=discarded,
    )


def search_path(workspace: str) -> str:
    return f"/2.0/workspaces/{workspace}/search/code"


def _clean(query: str) -> str:
    candidate = " ".join(query.split())
    if not candidate:
        raise UnsafeQuery("Give something to search for.")

    modifier = _MODIFIER.search(candidate)
    if modifier:
        raise UnsafeQuery(
            f"Remove {modifier.group(1)!r} from the query. This server chooses what is "
            "searched from the repository argument, and a query that carries its own "
            "scope would be choosing for it."
        )
    return candidate


def repository_of(value: dict) -> Repository | None:
    """Where Bitbucket says this result came from.

    A search result carries no repository field. The only statement of origin is the
    `self` link on the file, whose path is `/2.0/repositories/{workspace}/{repo}/src/...`
    — so that is what the allowlist check reads. A result whose origin cannot be read is
    discarded rather than assumed to be local: this filter is the backstop for a query
    that reached further than it should have, and a backstop that guesses is not one.
    """
    href = (((value.get("file") or {}).get("links") or {}).get("self") or {}).get("href")
    if not isinstance(href, str):
        return None

    found = _ORIGIN.search(urlsplit(href).path)
    return Repository(found.group("workspace"), found.group("repo")) if found else None


def _matches(value: dict) -> list[Match]:
    path = str((value.get("file") or {}).get("path") or "")
    found: list[Match] = []

    for content_match in value.get("content_matches") or []:
        for line in (content_match or {}).get("lines") or []:
            text = "".join(
                str((segment or {}).get("text") or "") for segment in line.get("segments") or []
            )
            found.append(Match(path=path, line=_line_number(line), text=text))

    return found or [Match(path=path, line=0, text="(matched on the file name)")]


def _line_number(line: dict) -> int:
    value = line.get("line")
    return value if isinstance(value, int) else 0

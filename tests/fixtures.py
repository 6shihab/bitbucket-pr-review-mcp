"""Bitbucket response fixtures.

These are synthetic — a pull request with a rename, a binary file and a lockfile, which
no single real pull request conveniently has. Their **shapes** are not guesses any more:
every field name and nesting here is checked against recorded responses in
`tests/recorded/`, exercised by `test_recorded_responses.py`. Two things that recording
corrected are reflected below — the Review Basis is Bitbucket's abbreviated twelve
characters rather than a full hash, and the diffstat entry carries no binary flag.
"""

from __future__ import annotations

from typing import Any

OUR_ACCOUNT_ID = "5f8a1b2c3d4e5f6071829304"

BASIS = "9f2c4a1b7e5d"
OLD_BASIS = "111111111111"


def pull_request(
    *,
    pull_request_id: int = 42,
    state: str = "OPEN",
    basis: str = BASIS,
    description: str = "Adds a retry around the flaky upstream call.",
) -> dict[str, Any]:
    return {
        "type": "pullrequest",
        "id": pull_request_id,
        "title": "Retry the upstream call",
        "state": state,
        "description": description,
        "author": {"display_name": "Anwar Hossain", "nickname": "anwar"},
        "source": {
            "branch": {"name": "feature/retry-upstream"},
            "commit": {"hash": basis},
        },
        "destination": {
            "branch": {"name": "main"},
            "commit": {"hash": "0a1b2c3d4e5f60718293a4b5c6d7e8f901234567"},
        },
        "created_on": "2026-08-20T09:14:22.000000+00:00",
        "updated_on": "2026-08-21T11:02:47.000000+00:00",
        "comment_count": 3,
        "task_count": 0,
        "close_source_branch": True,
        "links": {
            "html": {"href": "https://bitbucket.org/streamstech/db-explorer/pull-requests/42"},
            "diff": {
                "href": (
                    "https://api.bitbucket.org/2.0/repositories/streamstech/db-explorer/"
                    "diff/streamstech/db-explorer:9f2c4a1b7e5d%0D0a1b2c3d4e5f"
                )
            },
        },
    }


def current_user(display_name: str = "Anwar Hossain") -> dict[str, Any]:
    return {
        "type": "user",
        "display_name": display_name,
        "nickname": "anwar",
        "account_id": OUR_ACCOUNT_ID,
        "uuid": "{b4d2e8f1-3c5a-4e7b-9d1f-2a6c8e0b4d7f}",
    }


UNIFIED_DIFF = """\
diff --git a/src/app/retry.py b/src/app/retry.py
index 1a2b3c4..5d6e7f8 100644
--- a/src/app/retry.py
+++ b/src/app/retry.py
@@ -12,7 +12,11 @@ def call_upstream(payload):
     session = build_session()
-    return session.post(UPSTREAM, json=payload)
+    for attempt in range(RETRIES):
+        try:
+            return session.post(UPSTREAM, json=payload)
+        except TimeoutError:
+            sleep(backoff(attempt))
+    raise UpstreamUnavailable(UPSTREAM)
 
 
 def build_session():
@@ -40,3 +44,3 @@ def build_session():
-    return requests.Session()
+    return requests.Session()  # pooled
 
diff --git a/tests/test_retry.py b/tests/test_retry.py
new file mode 100644
index 0000000..9e8d7c6
--- /dev/null
+++ b/tests/test_retry.py
@@ -0,0 +1,4 @@
+def test_retries_on_timeout():
+    assert call_upstream({}) is not None
+
+
diff --git a/assets/logo.png b/assets/logo.png
index 3c4d5e6..7f8a9b0 100644
Binary files a/assets/logo.png and b/assets/logo.png differ
diff --git a/uv.lock b/uv.lock
index aaaaaaa..bbbbbbb 100644
--- a/uv.lock
+++ b/uv.lock
@@ -101,6 +101,6 @@ name = "httpx"
-version = "0.27.2"
+version = "0.28.1"
 
diff --git a/docs/old-notes.md b/docs/notes.md
similarity index 92%
rename from docs/old-notes.md
rename to docs/notes.md
index 1111111..2222222 100644
--- a/docs/old-notes.md
+++ b/docs/notes.md
@@ -1,3 +1,3 @@
-# Old notes
+# Notes
 
"""


def diffstat(*, next_page: str | None = None) -> dict[str, Any]:
    """The manifest endpoint's shape: one entry per changed file."""
    values = [
        _diffstat_entry("modified", "src/app/retry.py", "src/app/retry.py", 6, 2),
        _diffstat_entry("added", None, "tests/test_retry.py", 4, 0),
        _diffstat_entry("modified", "assets/logo.png", "assets/logo.png", 0, 0),
        _diffstat_entry("modified", "uv.lock", "uv.lock", 1, 1),
        _diffstat_entry("renamed", "docs/old-notes.md", "docs/notes.md", 1, 1),
        _diffstat_entry("removed", "src/app/legacy.py", None, 0, 31),
    ]
    payload: dict[str, Any] = {"pagelen": 50, "size": len(values), "values": values}
    if next_page:
        payload["next"] = next_page
    return payload


def _diffstat_entry(
    status: str, old: str | None, new: str | None, added: int, removed: int
) -> dict[str, Any]:
    return {
        "type": "diffstat",
        "status": status,
        "lines_added": added,
        "lines_removed": removed,
        "old": None if old is None else {"path": old, "type": "commit_file"},
        "new": None if new is None else {"path": new, "type": "commit_file"},
    }


def repository() -> dict[str, Any]:
    """GET /2.0/repositories/{workspace}/{repo}."""
    return {
        "type": "repository",
        "full_name": "streamstech/db-explorer",
        "name": "db-explorer",
        "description": "Explores databases. Carefully.",
        "is_private": True,
        "language": "python",
        "size": 4_812_233,
        "mainbranch": {"type": "branch", "name": "main"},
        "created_on": "2025-03-11T08:02:19.000000+00:00",
        "updated_on": "2026-08-21T11:02:47.000000+00:00",
        "links": {"html": {"href": "https://bitbucket.org/streamstech/db-explorer"}},
    }


def directory() -> dict[str, Any]:
    """GET /2.0/repositories/{workspace}/{repo}/src/{ref}/{path} for a directory."""
    return {
        "pagelen": 100,
        "values": [
            {
                "path": "src/app/retry.py",
                "type": "commit_file",
                "size": 2_104,
                "mimetype": None,
                "commit": {"hash": BASIS},
            },
            {
                "path": "src/app/handlers",
                "type": "commit_directory",
                "commit": {"hash": BASIS},
            },
            {
                "path": "src/app/__init__.py",
                "type": "commit_file",
                "size": 0,
                "mimetype": None,
                "commit": {"hash": BASIS},
            },
        ],
    }


def commits() -> dict[str, Any]:
    """GET .../commits/{ref} and .../pullrequests/{id}/commits share this shape."""
    return {
        "pagelen": 50,
        "values": [
            {
                "type": "commit",
                "hash": BASIS,
                "date": "2026-08-21T11:02:47+00:00",
                "message": "Retry the upstream call\n\nThe timeout was not the problem.",
                "author": {
                    "raw": "Anwar Hossain <anwar@streamstech.com>",
                    "user": {"display_name": "Anwar Hossain", "account_id": "5f8a1b2c"},
                },
                "parents": [{"hash": OLD_BASIS}],
            },
            {
                "type": "commit",
                "hash": OLD_BASIS,
                "date": "2026-08-20T09:14:22+00:00",
                "message": "Pool the session",
                "author": {"raw": "someone@example.com"},
                "parents": [],
            },
        ],
    }


def search(*, full_name: str = "streamstech/db-explorer") -> dict[str, Any]:
    """GET /2.0/workspaces/{workspace}/search/code."""
    return {
        "size": 1,
        "page": 1,
        "pagelen": 25,
        "values": [
            {
                "type": "code_search_result",
                "content_match_count": 1,
                "content_matches": [
                    {
                        "lines": [
                            {
                                "line": 88,
                                "segments": [
                                    {"text": "    return "},
                                    {"text": "call_upstream", "match": True},
                                    {"text": "(payload)"},
                                ],
                            }
                        ]
                    }
                ],
                "path_matches": [],
                "file": {
                    "path": "src/app/handlers/orders.py",
                    "type": "commit_file",
                    "links": {
                        "self": {
                            "href": (
                                f"https://api.bitbucket.org/2.0/repositories/{full_name}"
                                f"/src/{BASIS}/src/app/handlers/orders.py"
                            )
                        }
                    },
                },
            }
        ],
    }


def comments() -> dict[str, Any]:
    """GET .../pullrequests/{id}/comments — a conversation with one of ours in it."""
    return {
        "pagelen": 100,
        "size": 5,
        "values": [
            _comment(
                1001,
                "Nusrat Jahan",
                "9a8b7c6d5e4f",
                "The retry loop needs a ceiling, or a slow upstream becomes an outage.",
            ),
            _comment(
                1002,
                "Anwar Hossain",
                OUR_ACCOUNT_ID,
                "HIGH severity: `RETRIES` is read before it is defined on the failure path.",
                inline={"path": "src/app/retry.py", "from": None, "to": 14},
            ),
            _comment(
                1003,
                "Anwar Hossain",
                OUR_ACCOUNT_ID,
                "MEDIUM: this session is never closed.",
                inline={"path": "src/app/retry.py", "from": None, "to": 51, "outdated": True},
            ),
            _comment(
                1004,
                "Nusrat Jahan",
                "9a8b7c6d5e4f",
                "This line was doing something. Why remove it?",
                inline={"path": "src/app/retry.py", "from": 40, "to": None},
            ),
            _comment(
                1005,
                "Md Shihabul Hasan Shihab",
                "1122334455",
                "Agreed, will add the ceiling.",
                parent_id=1002,
            ),
        ],
    }


def _comment(
    comment_id: int,
    author: str,
    account_id: str,
    body: str,
    *,
    inline: dict[str, Any] | None = None,
    parent_id: int | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "id": comment_id,
        "type": "pullrequest_comment",
        "created_on": "2026-08-22T09:14:22.000000+00:00",
        "updated_on": "2026-08-22T09:14:22.000000+00:00",
        "content": {"raw": body, "markup": "markdown", "html": f"<p>{body}</p>"},
        "user": {"type": "user", "display_name": author, "account_id": account_id},
        "deleted": False,
        "pending": False,
        "links": {"self": {"href": "https://api.bitbucket.org/2.0/..."}},
    }
    if inline is not None:
        payload["inline"] = inline
    if parent_id is not None:
        payload["parent"] = {"id": parent_id}
    return payload

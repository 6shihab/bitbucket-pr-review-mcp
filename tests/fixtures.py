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
        "account_id": "5f8a1b2c3d4e5f6071829304",
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

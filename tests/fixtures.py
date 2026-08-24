"""Bitbucket response fixtures.

NOTE: these are modelled on Bitbucket Cloud's documented response shapes, not captured
from a live API — capturing real responses needs a credential and a real pull request,
which ticket 01 could not do. Ticket 02 brings the credential; the first real capture
should replace these bodies, keeping the field names and nesting honest. See the
"Fixtures" note in the spec for why this matters: hand-written fixtures agree with our
misunderstandings, recorded ones do not.
"""

from __future__ import annotations

from typing import Any

BASIS = "9f2c4a1b7e5d3c8a6b4f2e0d9c7b5a3f1e8d6c4b"
OLD_BASIS = "1111111111111111111111111111111111111111"


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

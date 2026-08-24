"""Ask Bitbucket who a credential belongs to, before trusting it with anything.

`GET /2.0/user` is the cheapest question that distinguishes the three failures a pasted
credential actually has: the wrong identifier (an Atlassian email is required, and a
Bitbucket username returns a bare 401 — ADR-0003), a wrong or revoked token, and a token
whose scopes are wider than this server will hold (`scopes.py`).

The display name that comes back is shown to the Reviewer before anything is stored,
because "you are about to post as Anwar Hossain" is the only check that catches a token
pasted from the wrong Atlassian account — which no error code ever will.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

import httpx

from .client import BitbucketClient
from .credentials import Credential
from .scopes import SCOPE_HEADER, ScopeVerdict, review_scopes
from .settings import Allowlist


@dataclass(frozen=True, slots=True)
class Identity:
    """Whose account a credential speaks for, and what it is permitted to do."""

    display_name: str
    account_id: str
    scopes: ScopeVerdict


async def verify_credential(
    credential: Credential,
    allowlist: Allowlist,
    make_http: Callable[[], httpx.AsyncClient],
) -> Identity:
    """Confirm the credential works and read back its scopes. Raises BitbucketError."""
    client = BitbucketClient(http=make_http(), allowlist=allowlist, credential=credential)
    try:
        response = await client.request("GET", "/2.0/user")
        body = response.json()
        return Identity(
            display_name=body.get("display_name") or credential.email,
            account_id=body.get("account_id", ""),
            scopes=review_scopes(response.headers.get(SCOPE_HEADER)),
        )
    finally:
        await client.aclose()

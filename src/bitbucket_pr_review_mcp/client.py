"""The only thing in this project that talks to Bitbucket.

Every request passes `assert_permitted` before the transport sees it, so the chokepoint
in ADR-0002 cannot be bypassed by adding a method here — a new call site inherits the
guard by construction rather than by remembering.

The HTTP client is injected rather than built, which is the project's one invented test
seam: tests supply a mock transport and everything above the wire stays production code.
This module knows nothing about MCP.
"""

from __future__ import annotations

from typing import Any

import httpx
from loguru import logger

from .credentials import Credential
from .guard import Forbidden, assert_permitted
from .settings import Allowlist

__all__ = ["BitbucketClient", "BitbucketError", "Forbidden", "NotFound", "Unauthorized"]

API_BASE = "https://api.bitbucket.org"


class BitbucketError(RuntimeError):
    """Bitbucket refused or failed a request that this server was allowed to make."""


class Unauthorized(BitbucketError):
    """The credential was rejected. Usually the wrong identifier, not the wrong token."""


class NotFound(BitbucketError):
    """Bitbucket has no such resource — or the credential cannot see it."""


class BitbucketClient:
    """A guarded, authenticated view of the Bitbucket Cloud REST API."""

    def __init__(
        self,
        http: httpx.AsyncClient,
        allowlist: Allowlist,
        credential: Credential,
    ) -> None:
        self._http = http
        self._allowlist = allowlist
        self._credential = credential

    async def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: Any | None = None,
    ) -> httpx.Response:
        """Issue one request, or refuse it before anything leaves the process."""
        assert_permitted(method, path, self._allowlist)

        response = await self._http.request(
            method,
            path,
            params=params,
            json=json,
            headers={
                "Authorization": self._credential.authorization_header(),
                "Accept": "application/json",
            },
        )
        return self._checked(response)

    async def get_json(self, path: str, *, params: dict[str, Any] | None = None) -> Any:
        response = await self.request("GET", path, params=params)
        return response.json()

    async def aclose(self) -> None:
        await self._http.aclose()

    def _checked(self, response: httpx.Response) -> httpx.Response:
        if response.is_success:
            return response

        target = f"{response.request.method} {response.request.url.path}"
        if response.status_code == 401:
            raise Unauthorized(
                "Bitbucket rejected the credential. The Basic authentication username must "
                "be your Atlassian account email — a Bitbucket username or the token's name "
                "returns exactly this error with nothing useful in the body."
            )
        if response.status_code == 403:
            raise BitbucketError(
                f"Bitbucket refused {target}. The token is probably missing a scope: this "
                "server needs repository read and pull request write."
            )
        if response.status_code == 404:
            raise NotFound(
                f"Bitbucket has no {target}. A private repository the credential cannot see "
                "also answers 404, so check the reference and the account's access."
            )

        logger.debug("Bitbucket returned {} for {}", response.status_code, target)
        raise BitbucketError(f"Bitbucket returned {response.status_code} for {target}.")


def build_http_client(timeout_seconds: float) -> httpx.AsyncClient:
    """The real transport. Tests never call this — they inject their own."""
    return httpx.AsyncClient(
        base_url=API_BASE,
        timeout=timeout_seconds,
        follow_redirects=False,
    )

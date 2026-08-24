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

# Bitbucket pages at 10 by default and 100 at most; these bound a walk, not a page.
MAX_PAGES = 20
MAX_REDIRECTS = 3


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
        return self._checked(await self._send(method, path, params=params, json=json))

    async def get_json(self, path: str, *, params: dict[str, Any] | None = None) -> Any:
        response = await self.request("GET", path, params=params)
        return response.json()

    async def get_pages(
        self,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        page_cap: int = MAX_PAGES,
    ) -> tuple[list[Any], bool]:
        """Walk a paginated collection. Returns the values and whether more remain.

        The cap is a real answer rather than a silent one: a pull request with ten
        thousand changed files gets what fits and a Caller told that it was cut short.
        """
        values: list[Any] = []
        next_url: str | None = path
        query = params

        for _ in range(page_cap):
            payload = await self.get_json(next_url, params=query)
            values.extend(payload.get("values") or [])
            next_url, query = payload.get("next"), None
            if not next_url:
                return values, False

        return values, True

    async def get_text(self, path: str, *, params: dict[str, Any] | None = None) -> str:
        """GET something that is not JSON — a diff, a raw file.

        Bitbucket answers the diff endpoints with a redirect to the same host, so one
        hop is followed here rather than by httpx: the redirect target goes back through
        `assert_permitted` (ADR-0002), which a transport-level redirect would skip.
        """
        response = await self._send("GET", path, accept="text/plain")

        for _ in range(MAX_REDIRECTS):
            if not response.is_redirect:
                break
            location = response.headers.get("location", "")
            if not location:
                break
            response = await self._send("GET", location, accept="text/plain")

        return self._checked(response).text

    async def _send(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: Any | None = None,
        accept: str = "application/json",
    ) -> httpx.Response:
        """The one place a request leaves this process, guard first."""
        assert_permitted(method, path, self._allowlist)

        return await self._http.request(
            method,
            path,
            params=params,
            json=json,
            headers={
                "Authorization": self._credential.authorization_header(),
                "Accept": accept,
            },
        )

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

"""Verifying a credential against Bitbucket, over the transport seam."""

from __future__ import annotations

import httpx
import pytest

from bitbucket_pr_review_mcp.client import Unauthorized
from bitbucket_pr_review_mcp.credentials import Credential, CredentialError
from bitbucket_pr_review_mcp.verify import verify_credential

from . import fixtures

GOOD_SCOPES = "read:repository:bitbucket, write:pullrequest:bitbucket"


async def verify(wire, allowlist, credential):
    return await verify_credential(
        credential,
        allowlist,
        lambda: httpx.AsyncClient(transport=wire.transport(), base_url="https://api.bitbucket.org"),
    )


class TestVerifying:
    async def test_returns_the_display_name_bitbucket_knows(self, wire, allowlist, credential):
        wire.will_return(
            httpx.Response(
                200,
                json=fixtures.current_user(display_name="Anwar Hossain"),
                headers={"x-oauth-scopes": GOOD_SCOPES},
            )
        )

        identity = await verify(wire, allowlist, credential)

        assert identity.display_name == "Anwar Hossain"
        assert identity.scopes.acceptable and identity.scopes.complete

    async def test_asks_only_who_am_i(self, wire, allowlist, credential):
        wire.will_return(httpx.Response(200, json=fixtures.current_user()))

        await verify(wire, allowlist, credential)

        assert wire.last.url.path == "/2.0/user"
        assert wire.last.method == "GET"

    async def test_a_rejected_credential_says_the_username_must_be_an_email(
        self, wire, allowlist, credential
    ):
        wire.will_return(httpx.Response(401, text=""))

        with pytest.raises(Unauthorized) as caught:
            await verify(wire, allowlist, credential)

        assert "email" in str(caught.value).lower()

    async def test_an_over_broad_token_is_reported_rather_than_hidden(
        self, wire, allowlist, credential
    ):
        wire.will_return(
            httpx.Response(
                200,
                json=fixtures.current_user(),
                headers={"x-oauth-scopes": f"{GOOD_SCOPES}, admin:repository:bitbucket"},
            )
        )

        identity = await verify(wire, allowlist, credential)

        assert not identity.scopes.acceptable

    async def test_a_bitbucket_username_never_reaches_the_network(self, wire, allowlist):
        with pytest.raises(CredentialError):
            await verify(wire, allowlist, Credential(email="anwar", token="t"))

        assert not wire.called

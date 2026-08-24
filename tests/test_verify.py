"""Verifying a credential against Bitbucket, over the transport seam."""

from __future__ import annotations

import httpx
import pytest

from bitbucket_pr_review_mcp.client import BitbucketError, Unauthorized
from bitbucket_pr_review_mcp.credentials import Credential, CredentialError
from bitbucket_pr_review_mcp.verify import verify_credential

from . import fixtures

GOOD_SCOPES = (
    "read:user:bitbucket, read:repository:bitbucket, "
    "read:pullrequest:bitbucket, write:pullrequest:bitbucket"
)


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


class TestATokenMissingTheUserScope:
    """The failure a real token hit: repository and pull request scopes granted, the
    user scope not, so /2.0/user answers 403 and nothing can say who we post as."""

    async def test_the_error_names_the_scope_bitbucket_asked_for(self, wire, allowlist, credential):
        wire.will_return(
            httpx.Response(
                403,
                json={
                    "type": "error",
                    "error": {
                        "message": "Your credentials lack one or more required privilege scopes.",
                        "detail": {
                            "required": ["read:user:bitbucket"],
                            "granted": ["read:repository:bitbucket", "read:pullrequest:bitbucket"],
                        },
                    },
                },
            )
        )

        with pytest.raises(BitbucketError) as caught:
            await verify(wire, allowlist, credential)

        assert "read:user:bitbucket" in str(caught.value)
        assert "--setup" in str(caught.value)

    async def test_a_403_without_detail_still_names_what_is_needed(
        self, wire, allowlist, credential
    ):
        wire.will_return(httpx.Response(403, text="nope"))

        with pytest.raises(BitbucketError) as caught:
            await verify(wire, allowlist, credential)

        assert "write:pullrequest:bitbucket" in str(caught.value)

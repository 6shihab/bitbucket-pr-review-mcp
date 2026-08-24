"""The single guard every outbound request passes.

ADR-0002 is blunt about why this exists: `write:pullrequest:bitbucket` grants comment
and merge as one indivisible unit, so Bitbucket cannot enforce this server's ceiling for
us. Mechanism 1 is that no merge tool exists. This is mechanism 2, and it is the one
that survives a future tool forgetting the rule, a bug in path construction, or a
refactor that widens something by accident — it does not care how the request was formed.

Every refusal asserts the transport was never touched. A guard that raises *after* the
request has gone is not a guard.
"""

from __future__ import annotations

import pytest

from bitbucket_pr_review_mcp.client import Forbidden

ALLOWED = "/2.0/repositories/streamstech/db-explorer"
FORBIDDEN_REPO = "/2.0/repositories/streamstech/secret-payroll"


class TestStateChangesAreRefused:
    @pytest.mark.parametrize(
        "method,path",
        [
            ("POST", f"{ALLOWED}/pullrequests/42/merge"),
            ("POST", f"{ALLOWED}/pullrequests/42/approve"),
            ("DELETE", f"{ALLOWED}/pullrequests/42/approve"),
            ("POST", f"{ALLOWED}/pullrequests/42/decline"),
            ("POST", f"{ALLOWED}/pullrequests/42/request-changes"),
            ("POST", f"{ALLOWED}/pullrequests"),
            ("PUT", f"{ALLOWED}/pullrequests/42"),
        ],
    )
    async def test_refuses_changing_a_pull_request(self, client, wire, method, path):
        with pytest.raises(Forbidden):
            await client.request(method, path)

        assert not wire.called, "the request must not leave the process"

    @pytest.mark.parametrize(
        "method,path",
        [
            ("POST", f"{ALLOWED}/src"),
            ("PUT", f"{ALLOWED}/src/main/README.md"),
            ("POST", f"{ALLOWED}/refs/branches"),
            ("PUT", f"{ALLOWED}"),
            ("POST", "/2.0/repositories/streamstech"),
        ],
    )
    async def test_refuses_writing_to_the_repository(self, client, wire, method, path):
        with pytest.raises(Forbidden):
            await client.request(method, path)

        assert not wire.called


class TestDeleteIsRefusedEverywhere:
    @pytest.mark.parametrize(
        "path",
        [
            f"{ALLOWED}/pullrequests/42/comments/9",
            f"{ALLOWED}/pullrequests/42",
            f"{ALLOWED}/src/main/README.md",
            "/2.0/user",
        ],
    )
    async def test_nothing_in_this_server_deletes(self, client, wire, path):
        with pytest.raises(Forbidden):
            await client.request("DELETE", path)

        assert not wire.called

    async def test_the_refusal_explains_that_this_is_deliberate(self, client):
        with pytest.raises(Forbidden) as caught:
            await client.request("DELETE", f"{ALLOWED}/pullrequests/42/comments/9")

        assert "delete" in str(caught.value).lower()


class TestHoweverTheyAreConstructed:
    """The point of a chokepoint is that it does not care how the URL was built."""

    @pytest.mark.parametrize(
        "path",
        [
            f"{ALLOWED}/pullrequests/42/comments/../merge",
            f"{ALLOWED}/pullrequests/42/./merge",
            f"{ALLOWED}//pullrequests//42//merge",
            f"{ALLOWED}/pullrequests/42/merge/",
            f"{ALLOWED}/pullrequests/42/MERGE",
        ],
    )
    async def test_refuses_a_disguised_merge(self, client, wire, path):
        with pytest.raises(Forbidden):
            await client.request("POST", path)

        assert not wire.called

    async def test_a_query_string_cannot_smuggle_a_permitted_path(self, client, wire):
        with pytest.raises(Forbidden):
            await client.request("POST", f"{ALLOWED}/pullrequests/42/merge?x=/comments")

        assert not wire.called

    async def test_refuses_an_absolute_url_to_another_host(self, client, wire):
        with pytest.raises(Forbidden):
            await client.request("GET", "https://evil.example.com/2.0/user")

        assert not wire.called


class TestTheAllowlistBoundsEverything:
    async def test_refuses_reading_an_unlisted_repository(self, client, wire):
        with pytest.raises(Forbidden) as caught:
            await client.request("GET", f"{FORBIDDEN_REPO}/pullrequests/1")

        assert not wire.called
        assert "secret-payroll" in str(caught.value)

    async def test_refuses_commenting_on_an_unlisted_repository(self, client, wire):
        with pytest.raises(Forbidden):
            await client.request("POST", f"{FORBIDDEN_REPO}/pullrequests/1/comments")

        assert not wire.called

    async def test_the_refusal_names_what_is_permitted(self, client):
        with pytest.raises(Forbidden) as caught:
            await client.request("GET", f"{FORBIDDEN_REPO}/pullrequests/1")

        assert "streamstech/db-explorer" in str(caught.value)


class TestWhatIsPermitted:
    async def test_reading_a_listed_repository(self, client, wire):
        await client.request("GET", f"{ALLOWED}/pullrequests/42")

        assert wire.called

    async def test_reading_the_current_user(self, client, wire):
        # Needed to verify a credential and to know whose comments are ours.
        await client.request("GET", "/2.0/user")

        assert wire.called

    async def test_posting_a_comment(self, client, wire):
        await client.request("POST", f"{ALLOWED}/pullrequests/42/comments", json={})

        assert wire.called

    async def test_updating_a_comment(self, client, wire):
        await client.request("PUT", f"{ALLOWED}/pullrequests/42/comments/9", json={})

        assert wire.called


class TestCredentialHandling:
    async def test_authenticates_with_basic_auth_on_every_request(self, client, wire):
        await client.request("GET", "/2.0/user")

        assert wire.last.headers["authorization"].startswith("Basic ")

    async def test_the_token_never_appears_in_a_refusal_message(self, client):
        with pytest.raises(Forbidden) as caught:
            await client.request("POST", f"{ALLOWED}/pullrequests/42/merge")

        assert "not-a-real-token" not in str(caught.value)

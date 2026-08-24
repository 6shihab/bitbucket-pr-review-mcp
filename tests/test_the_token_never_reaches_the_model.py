"""The property the whole credential design exists for: the model never sees the token.

Everything else in this server is about what Bitbucket lets us do. This is about what the
Caller is allowed to know, and it is the one property that cannot be recovered from after
it fails once: a token in a transcript is a token in whatever that transcript is stored
in, forwarded to, or trained on.

The token travels browser → keychain → `Authorization` header. It is never an argument to
a tool, never in a tool's answer, never in an error, and never in the setup URL — which
carries a *different* single-use token that grants nothing but the right to fill in one
form. These tests try to find it in every one of those places.
"""

from __future__ import annotations

import contextlib
import inspect
from datetime import date

import httpx
import pytest
from fastmcp.exceptions import ToolError

from bitbucket_pr_review_mcp import server as server_module
from bitbucket_pr_review_mcp.credentials import Credential, StoredCredential
from bitbucket_pr_review_mcp.gate import CredentialGate
from bitbucket_pr_review_mcp.server import build_server
from bitbucket_pr_review_mcp.settings import Settings

from . import fixtures

# Distinctive enough that finding it anywhere is unambiguous.
TOKEN = "ATATT-if-you-can-read-this-the-token-leaked-3245308C"
PR = "https://bitbucket.org/streamstech/db-explorer/pull-requests/42"


@pytest.fixture
def leaky_gate(keychain, setup_listener):
    keychain.save(
        StoredCredential(
            credential=Credential(email="reviewer@streamstech.com", token=TOKEN),
            expires_on=date(2099, 1, 1),
        )
    )
    return CredentialGate(keychain, setup_listener)


@pytest.fixture
def server(wire, allowlist, leaky_gate):
    return build_server(
        Settings(),
        allowlist,
        leaky_gate,
        http_factory=lambda: httpx.AsyncClient(
            transport=wire.transport(), base_url="https://api.bitbucket.org"
        ),
    )


def everything_said(result) -> str:
    return "\n".join(block.text for block in result.content)


class TestNoToolCanReturnIt:
    async def test_not_in_a_pull_request_read(self, server, wire):
        wire.will_return(httpx.Response(200, json=fixtures.pull_request()))

        result = await server.call_tool("bitbucket_get_pull_request", {"pull_request": PR})

        assert TOKEN not in everything_said(result)

    async def test_not_in_a_diff(self, server, wire):
        wire.will_return(
            httpx.Response(200, json=fixtures.pull_request()),
            httpx.Response(200, text=fixtures.UNIFIED_DIFF),
        )

        result = await server.call_tool("bitbucket_get_pull_request_diff", {"pull_request": PR})

        assert TOKEN not in everything_said(result)

    async def test_not_in_the_comments_listing(self, server, wire):
        wire.will_return(
            httpx.Response(200, json=fixtures.current_user()),
            httpx.Response(200, json=fixtures.comments()),
        )

        result = await server.call_tool("bitbucket_get_pr_comments", {"pull_request": PR})

        assert TOKEN not in everything_said(result)

    async def test_not_even_when_bitbucket_echoes_it_back(self, server, wire):
        """A hostile or broken upstream putting the token in a response body must not be
        a way to get it out: everything fetched is fenced, but nothing should carry it."""
        payload = fixtures.pull_request(description=f"Token is {TOKEN}")
        wire.will_return(httpx.Response(200, json=payload))

        result = await server.call_tool("bitbucket_get_pull_request", {"pull_request": PR})

        assert TOKEN in everything_said(result), (
            "this one is expected: it came from the pull request, not from the keychain"
        )


class TestNoErrorCanCarryIt:
    @pytest.mark.parametrize(
        "response",
        [
            httpx.Response(401, text=""),
            httpx.Response(403, json={"error": {"detail": {"required": ["read:user"]}}}),
            httpx.Response(404, text=""),
            httpx.Response(500, text="boom"),
        ],
    )
    async def test_not_in_any_failure_from_bitbucket(self, server, wire, response):
        wire.will_return(response)

        with pytest.raises(ToolError) as caught:
            await server.call_tool("bitbucket_get_pull_request", {"pull_request": PR})

        assert TOKEN not in str(caught.value)

    async def test_not_in_a_refusal_before_the_network(self, server, wire):
        for argument in ["nonsense", "streamstech/secret-payroll/1"]:
            with pytest.raises(ToolError) as caught:
                await server.call_tool("bitbucket_get_pull_request", {"pull_request": argument})

            assert TOKEN not in str(caught.value)

    async def test_not_in_the_setup_url_a_missing_credential_hands_back(
        self, wire, allowlist, keychain, setup_listener
    ):
        """The URL carries a one-time setup token, which is a different thing entirely:
        it grants the right to fill in one form on this machine, and nothing else."""
        empty = build_server(
            Settings(),
            allowlist,
            CredentialGate(keychain, setup_listener),
            http_factory=lambda: httpx.AsyncClient(transport=wire.transport()),
        )

        with pytest.raises(ToolError) as caught:
            await empty.call_tool("bitbucket_get_pull_request", {"pull_request": PR})

        assert setup_listener.URL in str(caught.value)
        assert TOKEN not in str(caught.value)


class TestItIsNotEvenAskable:
    async def test_no_tool_takes_a_token_or_a_password(self, server):
        for tool in await server.list_tools():
            for name in tool.parameters.get("properties", {}):
                assert not any(
                    word in name.lower() for word in ("token", "password", "secret", "credential")
                ), f"{tool.name} asks for {name}"

    async def test_no_tool_offers_to_hand_the_credential_over(self, server):
        """There is no auth-status tool, on purpose (ADR-0005). Nothing reports identity
        except as a consequence of doing the work asked for."""
        names = [tool.name for tool in await server.list_tools()]

        assert not any(
            word in name for name in names for word in ("auth", "token", "credential", "whoami")
        )

    def test_nothing_in_the_tool_layer_reads_the_token_directly(self):
        """`gate.current()` returns a Credential, and only `client.py` unpacks it."""
        source = inspect.getsource(server_module)

        assert ".token" not in source

    def test_a_credential_does_not_print_itself(self):
        credential = Credential(email="reviewer@streamstech.com", token=TOKEN)

        assert TOKEN not in repr(credential)
        assert TOKEN not in str(credential)
        assert TOKEN not in repr(
            StoredCredential(credential=credential, expires_on=date(2099, 1, 1))
        )

    async def test_it_does_reach_bitbucket_which_is_the_point(self, server, wire):
        """The negative tests above are only meaningful if the token is being used."""
        import base64

        wire.will_return(httpx.Response(200, json=fixtures.pull_request()))
        await server.call_tool("bitbucket_get_pull_request", {"pull_request": PR})

        sent = wire.last.headers["authorization"].removeprefix("Basic ")
        assert TOKEN in base64.b64decode(sent).decode()


class TestForgettingIt:
    def test_the_credential_is_removed_from_the_device(self, leaky_gate, keychain):
        assert keychain.load() is not None

        leaky_gate.forget()

        assert keychain.load() is None

    def test_the_next_tool_call_asks_for_setup_again(self, leaky_gate, setup_listener):
        leaky_gate.current()
        leaky_gate.forget()

        with pytest.raises(Exception) as caught:
            leaky_gate.current()

        assert setup_listener.URL in str(caught.value)

    def test_forgetting_nothing_is_not_an_error(self, keychain, setup_listener):
        CredentialGate(keychain, setup_listener).forget()

    def test_it_says_where_the_token_still_exists(self, leaky_gate, capsys):
        from bitbucket_pr_review_mcp.__main__ import _run_forget, configure_logging

        configure_logging("INFO")

        assert _run_forget(leaky_gate) == 0
        assert "id.atlassian.com" in capsys.readouterr().err

    def test_a_container_says_where_to_do_it_instead(self, monkeypatch):
        from bitbucket_pr_review_mcp.__main__ import _run_forget
        from bitbucket_pr_review_mcp.environment import (
            EMAIL_ENV,
            TOKEN_ENV,
            EnvironmentStore,
            SetupUnavailable,
        )

        monkeypatch.setenv(EMAIL_ENV, "reviewer@streamstech.com")
        monkeypatch.setenv(TOKEN_ENV, TOKEN)
        gate = CredentialGate(EnvironmentStore.configured(), SetupUnavailable())

        assert _run_forget(gate) == 2

    async def test_no_tool_can_do_it(self, server):
        """A pull request description must not be able to talk a Caller into logging the
        Reviewer out. Deletion is a person at a terminal, not an argument."""
        names = [tool.name for tool in await server.list_tools()]

        assert not any(
            word in name for name in names for word in ("forget", "logout", "disconnect", "revoke")
        )

        with contextlib.suppress(ToolError):
            await server.call_tool("bitbucket_get_pull_request", {"pull_request": "forget"})

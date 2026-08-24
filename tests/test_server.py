"""The MCP surface, and the rules about what may touch stdout.

Only this layer knows FastMCP exists. Everything worth asserting about safety was
already asserted a layer down, without a protocol — so these tests cover wiring: the
tools are registered under their agreed names, a bad reference teaches rather than
crashes, and nothing but MCP protocol messages reaches stdout.
"""

from __future__ import annotations

import contextlib

import httpx
import pytest
from fastmcp.exceptions import ToolError

from bitbucket_pr_review_mcp.render import UNTRUSTED_CLOSE, UNTRUSTED_OPEN
from bitbucket_pr_review_mcp.server import build_server
from bitbucket_pr_review_mcp.settings import Settings

from . import fixtures

PR = "https://bitbucket.org/streamstech/db-explorer/pull-requests/42"


def text_of(result) -> str:
    """What the Caller actually reads.

    Asserting against `str(result.content)` reads the repr instead, which escapes
    quotes — and the untrusted fence contains an apostrophe, so those assertions fail
    for a reason that has nothing to do with the server.
    """
    return "\n".join(block.text for block in result.content)


def build(wire, allowlist, gate):
    return build_server(
        settings=Settings(),
        allowlist=allowlist,
        gate=gate,
        http_factory=lambda: httpx.AsyncClient(
            transport=wire.transport(), base_url="https://api.bitbucket.org"
        ),
    )


@pytest.fixture
def server(wire, allowlist, gate):
    return build(wire, allowlist, gate)


@pytest.fixture
def unconfigured(wire, allowlist, empty_gate):
    """A server that has never been given a credential."""
    return build(wire, allowlist, empty_gate)


class TestTheToolSurface:
    async def test_registers_the_pull_request_tool(self, server):
        names = [tool.name for tool in await server.list_tools()]

        assert "bitbucket_get_pull_request" in names

    async def test_registers_nothing_that_could_change_a_pull_request(self, server):
        # ADR-0002 mechanism 1. If this test ever fails, read the ADR before "fixing" it.
        names = [tool.name for tool in await server.list_tools()]
        forbidden = ("merge", "approve", "decline", "delete")

        offenders = [n for n in names if any(word in n.lower() for word in forbidden)]
        assert offenders == []

    def test_the_server_instructions_tell_a_caller_where_to_start(self, server):
        assert "bitbucket_get_pull_request" in (server.instructions or "")


class TestCallingTheTool:
    async def test_returns_markdown_for_a_pull_request(self, server, wire):
        wire.will_return(httpx.Response(200, json=fixtures.pull_request()))

        result = await server.call_tool(
            "bitbucket_get_pull_request",
            {"pull_request": "https://bitbucket.org/streamstech/db-explorer/pull-requests/42"},
        )

        assert "Retry the upstream call" in text_of(result)

    async def test_a_malformed_reference_teaches_the_accepted_forms(self, server, wire):
        with pytest.raises(ToolError) as caught:
            await server.call_tool("bitbucket_get_pull_request", {"pull_request": "nonsense"})

        assert "workspace/repo/id" in str(caught.value)
        assert not wire.called, "a reference we cannot read must not reach the network"

    async def test_an_unlisted_repository_is_refused_at_the_tool(self, server, wire):
        with pytest.raises(ToolError) as caught:
            await server.call_tool(
                "bitbucket_get_pull_request", {"pull_request": "streamstech/secret-payroll/1"}
            )

        assert "allowlist" in str(caught.value).lower()
        assert not wire.called


class TestStdoutIsSacred:
    """A stray line on stdout corrupts the MCP stream in a way that is miserable to debug."""

    def test_logging_goes_to_stderr_and_never_stdout(self, capsys):
        from loguru import logger

        from bitbucket_pr_review_mcp.__main__ import configure_logging

        configure_logging("DEBUG")
        logger.info("a log line")
        logger.error("an error line")

        captured = capsys.readouterr()
        assert captured.out == "", "nothing but MCP protocol messages may reach stdout"
        assert "a log line" in captured.err


class TestWithNoCredentialYet:
    """ADR-0004: a tool that cannot work says where the setup page is, rather than
    surfacing a 401 the Reviewer has no way to interpret."""

    async def test_the_tool_fails_with_the_setup_url(self, unconfigured, setup_listener):
        with pytest.raises(ToolError) as caught:
            await unconfigured.call_tool(
                "bitbucket_get_pull_request", {"pull_request": "streamstech/db-explorer/42"}
            )

        assert setup_listener.URL in str(caught.value)

    async def test_nothing_reaches_bitbucket_without_a_credential(self, unconfigured, wire):
        with pytest.raises(ToolError):
            await unconfigured.call_tool(
                "bitbucket_get_pull_request", {"pull_request": "streamstech/db-explorer/42"}
            )

        assert not wire.called

    async def test_no_tool_argument_can_open_the_listener(self, server, wire, setup_listener):
        """The listener opens on facts, never on input. A pull request description that
        could raise a credential form would be phishing aimed at the Reviewer."""
        wire.will_return(httpx.Response(200, json=fixtures.pull_request()))

        for argument in [
            "streamstech/db-explorer/42",
            "nonsense",
            "streamstech/secret-payroll/1",
            "https://bitbucket.org/streamstech/db-explorer/pull-requests/42?setup=1",
        ]:
            with contextlib.suppress(ToolError):
                await server.call_tool("bitbucket_get_pull_request", {"pull_request": argument})

        assert setup_listener.starts == 0


class TestWhenBitbucketRejectsTheCredential:
    async def test_a_401_reopens_setup_and_the_error_says_where(
        self, server, wire, setup_listener
    ):
        wire.will_return(httpx.Response(401, text=""))

        with pytest.raises(ToolError) as caught:
            await server.call_tool(
                "bitbucket_get_pull_request", {"pull_request": "streamstech/db-explorer/42"}
            )

        assert setup_listener.starts == 1
        assert setup_listener.URL in str(caught.value)


class TestTheDiffTools:
    """Wiring only — the parsing, slicing and caching are tested a layer down."""

    async def test_both_tools_are_registered(self, server):
        names = [tool.name for tool in await server.list_tools()]

        assert "bitbucket_get_pull_request_changes" in names
        assert "bitbucket_get_pull_request_diff" in names

    async def test_the_manifest_lists_the_changed_files(self, server, wire):
        wire.will_return(
            httpx.Response(200, json=fixtures.pull_request()),
            httpx.Response(200, json=fixtures.diffstat()),
        )

        result = await server.call_tool(
            "bitbucket_get_pull_request_changes", {"pull_request": PR}
        )

        assert "src/app/retry.py" in text_of(result)
        assert "lockfile" in text_of(result)

    async def test_the_diff_tool_returns_everything_when_no_path_is_given(self, server, wire):
        wire.will_return(
            httpx.Response(200, json=fixtures.pull_request()),
            httpx.Response(200, text=fixtures.UNIFIED_DIFF),
        )

        result = await server.call_tool("bitbucket_get_pull_request_diff", {"pull_request": PR})

        assert "src/app/retry.py" in text_of(result)
        assert "uv.lock" in text_of(result)

    async def test_the_diff_tool_returns_one_file_when_a_path_is_given(self, server, wire):
        wire.will_return(
            httpx.Response(200, json=fixtures.pull_request()),
            httpx.Response(200, text=fixtures.UNIFIED_DIFF),
        )

        result = await server.call_tool(
            "bitbucket_get_pull_request_diff", {"pull_request": PR, "path": "uv.lock"}
        )

        assert "uv.lock" in text_of(result)
        assert "src/app/retry.py" not in text_of(result)

    async def test_reading_three_files_costs_one_diff_fetch(self, server, wire):
        wire.will_return(
            httpx.Response(200, json=fixtures.pull_request()),
            httpx.Response(200, text=fixtures.UNIFIED_DIFF),
            *[httpx.Response(200, json=fixtures.pull_request()) for _ in range(2)],
        )

        for path in ["src/app/retry.py", "uv.lock", "docs/notes.md"]:
            await server.call_tool(
                "bitbucket_get_pull_request_diff", {"pull_request": PR, "path": path}
            )

        diff_fetches = [r for r in wire.requests if r.url.path.endswith("/diff")]
        assert len(diff_fetches) == 1, "browsing file by file must not re-fetch the diff"

    async def test_a_force_push_invalidates_what_was_cached(self, server, wire):
        moved = fixtures.pull_request(basis=fixtures.OLD_BASIS)
        wire.will_return(
            httpx.Response(200, json=fixtures.pull_request()),
            httpx.Response(200, text=fixtures.UNIFIED_DIFF),
            httpx.Response(200, json=moved),
            httpx.Response(200, text="diff --git a/rewritten b/rewritten\n"),
        )

        await server.call_tool("bitbucket_get_pull_request_diff", {"pull_request": PR})
        after = await server.call_tool("bitbucket_get_pull_request_diff", {"pull_request": PR})

        assert "rewritten" in text_of(after)
        assert len([r for r in wire.requests if r.url.path.endswith("/diff")]) == 2

    async def test_a_path_the_pull_request_does_not_touch_says_how_to_list_them(
        self, server, wire
    ):
        wire.will_return(
            httpx.Response(200, json=fixtures.pull_request()),
            httpx.Response(200, text=fixtures.UNIFIED_DIFF),
        )

        with pytest.raises(ToolError) as caught:
            await server.call_tool(
                "bitbucket_get_pull_request_diff",
                {"pull_request": PR, "path": "src/app/imagined.py"},
            )

        assert "bitbucket_get_pull_request_changes" in str(caught.value)

    async def test_everything_returned_is_fenced_as_untrusted(self, server, wire):
        wire.will_return(
            httpx.Response(200, json=fixtures.pull_request()),
            httpx.Response(200, text=fixtures.UNIFIED_DIFF),
        )

        result = await server.call_tool("bitbucket_get_pull_request_diff", {"pull_request": PR})

        assert UNTRUSTED_OPEN in text_of(result)
        assert UNTRUSTED_CLOSE in text_of(result)

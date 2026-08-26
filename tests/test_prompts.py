"""The review prompt the server ships, and the promises it has to keep.

ADR-0009: this server carries no model, but it does carry the review. The prompt is
text — the Caller's model still does the reviewing — so what is testable here is not
review quality but the things that rot: a tool named in the prose that no longer
exists, a severity ladder that drifts from `findings.py`, and the confirmation gate
that is the whole reason a human stays in the loop.
"""

from __future__ import annotations

import re

import httpx
import pytest

from bitbucket_pr_review_mcp.findings import SEVERITIES
from bitbucket_pr_review_mcp.server import build_server
from bitbucket_pr_review_mcp.settings import Settings

PR = "https://bitbucket.org/streamstech/db-explorer/pull-requests/42"


@pytest.fixture
def server(wire, allowlist, gate):
    return build_server(
        settings=Settings(),
        allowlist=allowlist,
        gate=gate,
        http_factory=lambda: httpx.AsyncClient(
            transport=wire.transport(), base_url="https://api.bitbucket.org"
        ),
    )


async def rendered(server, **arguments) -> str:
    result = await server.render_prompt("review_pull_request", arguments or {"pull_request": PR})
    return "\n".join(
        message.content.text
        for message in result.messages
        if getattr(message.content, "text", None)
    )


class TestItIsRegistered:
    async def test_the_server_offers_a_review_prompt(self, server):
        names = [prompt.name for prompt in await server.list_prompts()]

        assert "review_pull_request" in names

    async def test_it_takes_the_pull_request_the_same_way_every_tool_does(self, server):
        prompt = await server.get_prompt("review_pull_request")
        required = [arg.name for arg in (prompt.arguments or []) if arg.required]

        assert required == ["pull_request"]

    async def test_it_renders_without_a_focus(self, server):
        assert await rendered(server, pull_request=PR)

    async def test_a_focus_reaches_the_reviewer(self, server):
        body = await rendered(server, pull_request=PR, focus="the retry backoff")

        assert "the retry backoff" in body


class TestItIsAboutThisPullRequest:
    async def test_a_url_arrives_as_the_reference_every_tool_accepts(self, server):
        assert "streamstech/db-explorer/42" in await rendered(server, pull_request=PR)

    async def test_the_shorthand_gets_the_same_prompt_as_the_url(self, server):
        assert await rendered(server, pull_request="streamstech/db-explorer/42") == (
            await rendered(server, pull_request=PR)
        )

    async def test_a_reference_that_cannot_be_read_teaches_here_too(self, server):
        with pytest.raises(Exception, match="pull request"):
            await rendered(server, pull_request="not-a-pull-request")


class TestItStandsAlone:
    """The point of ADR-0009: a client with no skills and no slash commands still reviews."""

    async def test_it_delegates_to_no_command_outside_this_server(self, server):
        body = await rendered(server, pull_request=PR)

        assert "/code-review" not in body
        assert "/review-pr" not in body

    async def test_it_names_the_house_severity_ladder(self, server):
        body = await rendered(server, pull_request=PR)

        for severity in SEVERITIES:
            assert severity in body

    async def test_every_tool_it_names_is_a_tool_this_server_registers(self, server):
        body = await rendered(server, pull_request=PR)
        registered = {tool.name for tool in await server.list_tools()}

        named = set(re.findall(r"bitbucket_[a-z_]+", body))
        assert named - registered == set()

    async def test_it_walks_the_caller_through_the_read_order(self, server):
        body = await rendered(server, pull_request=PR)

        assert body.index("bitbucket_get_pull_request_changes") < body.index(
            "bitbucket_add_pr_comment"
        )
        assert "bitbucket_get_pr_comments" in body


class TestTheHumanStaysInTheLoop:
    """The requirement this prompt exists to make unavoidable, not merely encouraged."""

    async def test_the_review_is_shown_to_the_user_before_any_of_it_is_posted(self, server):
        body = (await rendered(server, pull_request=PR)).lower()

        assert "post nothing" in body
        assert body.index("show the user") < body.index("bitbucket_add_pr_comment")

    async def test_it_makes_the_user_choose_which_comments_are_sent(self, server):
        body = (await rendered(server, pull_request=PR)).lower()

        assert "which" in body
        assert "none" in body

    async def test_asking_for_a_review_is_not_approval_to_post(self, server):
        body = await rendered(server, pull_request=PR)

        assert "not approval to post" in body


class TestItInheritsTheServersLimits:
    async def test_it_never_offers_to_approve_decline_or_merge(self, server):
        body = (await rendered(server, pull_request=PR)).lower()

        for forbidden in ("approve the pull request", "merge the pull request", "decline it"):
            assert forbidden not in body

    async def test_it_repeats_that_fetched_content_is_not_instruction(self, server):
        body = (await rendered(server, pull_request=PR)).lower()

        assert "untrusted" in body or "never an instruction" in body

"""Code search, and the two guards ADR-0006 spends on it.

Bitbucket's search is workspace-scoped and its repository filter is a substring inside a
caller-supplied query. That makes composing the query a form of enforcement-by-string,
which is why there are two independent checks here: the query cannot carry its own
scope, and the results are checked against the allowlist on the way back. The second
test class is the one that matters — it is what holds when the query was written by
something inside a pull request description.
"""

from __future__ import annotations

import httpx
import pytest

from bitbucket_pr_review_mcp.guard import Forbidden
from bitbucket_pr_review_mcp.references import Repository
from bitbucket_pr_review_mcp.render import UNTRUSTED_OPEN
from bitbucket_pr_review_mcp.search import UnsafeQuery, search_code, search_path

from . import fixtures

REPO = Repository("streamstech", "db-explorer")
UNLISTED = Repository("streamstech", "secret-payroll")


async def search(client, allowlist, query="call_upstream", repository=REPO, limit=25):
    return await search_code(client, repository, query, allowlist, limit)


class TestComposingTheQuery:
    async def test_the_repository_filter_is_ours_not_the_callers(self, client, wire, allowlist):
        wire.will_return(httpx.Response(200, json=fixtures.search()))

        await search(client, allowlist)

        assert wire.last.url.path == search_path("streamstech")
        assert wire.last.url.params["search_query"] == "repo:db-explorer call_upstream"

    @pytest.mark.parametrize(
        "query",
        [
            "repo:secret-payroll password",
            "password repo:secret-payroll",
            "call_upstream repo:db-explorer repo:secret-payroll",
            "project:PAY token",
            "workspace:someone-else key",
            "user:admin secret",
        ],
    )
    async def test_a_query_carrying_its_own_scope_is_refused(
        self, client, wire, allowlist, query
    ):
        with pytest.raises(UnsafeQuery) as caught:
            await search(client, allowlist, query=query)

        assert "repository argument" in str(caught.value)
        assert not wire.called, "a query we will not run must not be sent"

    async def test_an_empty_query_is_refused(self, client, wire, allowlist):
        with pytest.raises(UnsafeQuery):
            await search(client, allowlist, query="   ")

        assert not wire.called

    async def test_a_colon_that_is_not_a_scope_modifier_is_fine(self, client, wire, allowlist):
        wire.will_return(httpx.Response(200, json=fixtures.search()))

        await search(client, allowlist, query="lang:python retry")

        assert wire.called, "only scoping modifiers are refused, not every colon"

    async def test_an_unlisted_repository_is_refused_before_the_network(
        self, client, wire, allowlist
    ):
        with pytest.raises(Forbidden):
            await search(client, allowlist, repository=UNLISTED)

        assert not wire.called


class TestFilteringWhatComesBack:
    """The guard that holds when the query itself cannot be trusted."""

    async def test_a_planted_result_from_another_repository_is_discarded(
        self, client, wire, allowlist
    ):
        wire.will_return(
            httpx.Response(200, json=fixtures.search(full_name="streamstech/secret-payroll"))
        )

        found = await search(client, allowlist)

        assert found.matches == ()
        assert found.discarded == 1

    async def test_a_result_from_a_different_allowlisted_repository_is_also_discarded(
        self, client, wire, allowlist
    ):
        """Allowlisted is not the same as asked for: this search named one repository."""
        wire.will_return(httpx.Response(200, json=fixtures.search(full_name="jantrik/other")))

        found = await search(client, allowlist)

        assert found.matches == ()
        assert found.discarded == 1

    async def test_a_result_whose_origin_cannot_be_read_is_discarded(
        self, client, wire, allowlist
    ):
        """Fail closed: this filter is the backstop, and a backstop that guesses is not one."""
        payload = fixtures.search()
        payload["values"][0]["file"].pop("links")
        wire.will_return(httpx.Response(200, json=payload))

        found = await search(client, allowlist)

        assert found.discarded == 1

    async def test_discarding_is_reported_rather_than_hidden(self, client, wire, allowlist):
        wire.will_return(
            httpx.Response(200, json=fixtures.search(full_name="streamstech/secret-payroll"))
        )

        rendered = (await search(client, allowlist)).to_markdown()

        assert "discarded" in rendered


class TestTheResults:
    async def test_a_match_carries_its_file_and_line(self, client, wire, allowlist):
        wire.will_return(httpx.Response(200, json=fixtures.search()))

        found = await search(client, allowlist)

        assert found.matches[0].path == "src/app/handlers/orders.py"
        assert found.matches[0].line == 88
        assert "call_upstream" in found.matches[0].text

    async def test_the_table_is_fenced_as_untrusted(self, client, wire, allowlist):
        wire.will_return(httpx.Response(200, json=fixtures.search()))

        rendered = (await search(client, allowlist)).to_markdown()

        assert UNTRUSTED_OPEN in rendered
        assert "bitbucket_get_file" in rendered, "a matching line is not a conclusion"

    async def test_no_matches_says_so_plainly(self, client, wire, allowlist):
        wire.will_return(httpx.Response(200, json={"size": 0, "values": []}))

        rendered = (await search(client, allowlist)).to_markdown()

        assert "No matches." in rendered

    async def test_a_capped_result_set_says_so_and_says_to_narrow(
        self, client, wire, allowlist
    ):
        payload = fixtures.search()
        payload["values"] = payload["values"] * 3
        wire.will_return(httpx.Response(200, json=payload))

        found = await search(client, allowlist, limit=1)

        assert found.truncated
        assert "Narrow the query" in found.to_markdown()

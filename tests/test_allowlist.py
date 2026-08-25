"""The Allowlisted Repository set, and the server's refusal to run without one.

ADR-0002 leans on this list: once browser-era credentials reach every repository the
Reviewer can, the allowlist is what bounds the damage a confused or injected Caller can
do. A bare wildcard would quietly restore "every repository I can reach" as the default,
so there is still no way to write one. `workspace/*` is the single exception, and it is a
narrower claim than it looks: it admits one *named* workspace, so what bounds this server
is still a decision somebody made rather than whatever the credential happens to reach.
It is also the widest entry that can be written, and it takes in repositories that did
not exist when it was written.
"""

import pytest

from bitbucket_pr_review_mcp.references import Repository
from bitbucket_pr_review_mcp.settings import (
    Allowlist,
    ConfigError,
    Settings,
    load_allowlist,
)


def write_config(tmp_path, body: str):
    path = tmp_path / "repositories.yaml"
    path.write_text(body, encoding="utf-8")
    return path


class TestRefusingToStart:
    def test_refuses_when_the_file_is_missing(self, tmp_path):
        with pytest.raises(ConfigError) as caught:
            load_allowlist(tmp_path / "absent.yaml")

        assert "repositories.yaml" in str(caught.value) or "absent.yaml" in str(caught.value)

    def test_refuses_an_empty_list(self, tmp_path):
        path = write_config(tmp_path, "repositories: []\n")

        with pytest.raises(ConfigError):
            load_allowlist(path)

    def test_refuses_a_file_with_no_repositories_key(self, tmp_path):
        path = write_config(tmp_path, "something_else: true\n")

        with pytest.raises(ConfigError):
            load_allowlist(path)

    @pytest.mark.parametrize("wildcard", ["*", "*/*", "*/db-explorer", "strea*/db-explorer"])
    def test_refuses_a_wildcard_in_the_workspace(self, tmp_path, wildcard):
        """The workspace is the boundary. A pattern there leaves no boundary at all."""
        path = write_config(tmp_path, f"repositories:\n  - {wildcard}\n")

        with pytest.raises(ConfigError) as caught:
            load_allowlist(path)

        assert "wildcard" in str(caught.value).lower()

    @pytest.mark.parametrize("wildcard", ["streamstech/db-*", "streamstech/*-explorer"])
    def test_refuses_a_partial_wildcard_in_the_repository(self, tmp_path, wildcard):
        """`workspace/*` is a decision somebody made once. `workspace/db-*` is a guess
        about naming, and it quietly takes in whatever gets named that way next."""
        path = write_config(tmp_path, f"repositories:\n  - {wildcard}\n")

        with pytest.raises(ConfigError) as caught:
            load_allowlist(path)

        assert "wildcard" in str(caught.value).lower()

    def test_refuses_an_entry_that_is_not_a_repository(self, tmp_path):
        path = write_config(tmp_path, "repositories:\n  - just-a-workspace\n")

        with pytest.raises(ConfigError):
            load_allowlist(path)

    def test_the_refusal_says_what_to_do_about_it(self, tmp_path):
        path = write_config(tmp_path, "repositories: []\n")

        with pytest.raises(ConfigError) as caught:
            load_allowlist(path)

        message = str(caught.value)
        assert "workspace/repo" in message, "should show the entry format"


class TestMembership:
    @pytest.fixture
    def allowlist(self, tmp_path):
        path = write_config(
            tmp_path,
            "repositories:\n  - streamstech/db-explorer\n  - streamstech/lent-manager\n",
        )
        return load_allowlist(path)

    def test_permits_a_listed_repository(self, allowlist):
        assert allowlist.permits(Repository("streamstech", "db-explorer"))

    def test_refuses_an_unlisted_repository(self, allowlist):
        assert not allowlist.permits(Repository("streamstech", "secret-payroll"))

    def test_refuses_an_unlisted_workspace(self, allowlist):
        assert not allowlist.permits(Repository("someone-else", "db-explorer"))

    def test_matching_ignores_case(self, allowlist):
        # Bitbucket slugs are case-insensitive in practice; a Caller echoing a URL with
        # different casing must not slip past the boundary either way.
        assert allowlist.permits(Repository("StreamsTech", "DB-Explorer"))

    def test_reports_what_it_permits(self, allowlist):
        assert allowlist.names() == ("streamstech/db-explorer", "streamstech/lent-manager")


class TestAWholeWorkspace:
    """`workspace/*` admits one named workspace entire. It is the widest entry that can
    be written here, so the surprising half — that it covers repositories nobody listed,
    including ones created later — is asserted rather than left to be discovered."""

    def test_it_is_accepted(self, tmp_path):
        path = write_config(tmp_path, "repositories:\n  - jantrik/*\n")

        assert load_allowlist(path).permits(Repository("jantrik", "admin-client"))

    def test_it_admits_a_repository_nobody_listed(self, tmp_path):
        path = write_config(tmp_path, "repositories:\n  - jantrik/*\n")

        assert load_allowlist(path).permits(Repository("jantrik", "invented-tomorrow"))

    def test_it_does_not_admit_another_workspace(self, tmp_path):
        path = write_config(tmp_path, "repositories:\n  - jantrik/*\n")

        assert not load_allowlist(path).permits(Repository("streamstech", "admin-client"))

    def test_it_ignores_case_like_every_other_entry(self, tmp_path):
        path = write_config(tmp_path, "repositories:\n  - Jantrik/*\n")

        assert load_allowlist(path).permits(Repository("jantrik", "admin-client"))

    def test_search_may_ask_that_workspace(self, tmp_path):
        """Code search is workspace-scoped (ADR-0006), so this is the question it asks."""
        path = write_config(tmp_path, "repositories:\n  - jantrik/*\n")

        assert load_allowlist(path).permits_workspace("jantrik")

    def test_it_mixes_with_named_repositories(self, tmp_path):
        path = write_config(
            tmp_path, "repositories:\n  - jantrik/*\n  - streamstech/db-explorer\n"
        )
        allowlist = load_allowlist(path)

        assert allowlist.permits(Repository("jantrik", "anything"))
        assert allowlist.permits(Repository("streamstech", "db-explorer"))
        assert not allowlist.permits(Repository("streamstech", "secret-payroll"))

    def test_it_is_named_as_what_it_is(self, tmp_path):
        """`--health` and every refusal print these. A whole workspace should not read
        as one repository that happens to be called `*`."""
        path = write_config(tmp_path, "repositories:\n  - jantrik/*\n")

        assert load_allowlist(path).names() == ("jantrik/*",)


class TestSayingSoOutLoud:
    """A whole workspace is the widest entry here, and the file it is written in gets
    read once. So every way of starting this server says it, and the shared deployment
    most of all — that is the one holding several people's merge-capable credentials."""

    def announced(self, allowlist, capsys) -> str:
        from bitbucket_pr_review_mcp.__main__ import (
            _announce_whole_workspaces,
            configure_logging,
        )

        configure_logging("INFO")
        _announce_whole_workspaces(allowlist)

        return capsys.readouterr().err

    def test_it_names_the_workspace(self, capsys):
        said = self.announced(Allowlist.of([], workspaces=["jantrik"]), capsys)

        assert "jantrik/*" in said

    def test_it_says_the_part_that_is_not_in_the_file(self, capsys):
        """That the entry covers repositories which did not exist when it was written."""
        said = self.announced(Allowlist.of([], workspaces=["jantrik"]), capsys)

        assert "created after" in said

    def test_an_enumerated_list_says_nothing(self, capsys):
        said = self.announced(Allowlist.of([Repository("jantrik", "admin-client")]), capsys)

        assert said.strip() == ""

    def test_the_shared_server_announces_it_too(self, tmp_path, monkeypatch, capsys):
        """`--http` does not run the stdio startup checks, so it needs its own call.
        Asserted because the first implementation of this warning skipped that path —
        the one deployment where the allowlist bounds more than one person's access."""
        from bitbucket_pr_review_mcp.__main__ import _run_http, configure_logging

        configure_logging("INFO")
        monkeypatch.setenv("BB_MCP_VAULT_KEY", "not-a-usable-key")
        allowlist = Allowlist.of([], workspaces=["jantrik"])

        # Stops with 2 on the unusable key, which is fine: the announcement is about
        # configuration and belongs before anything that can refuse to start.
        assert _run_http(Settings(public_url="", oidc_issuer=""), allowlist, "127.0.0.1", 0) == 2
        assert "jantrik/*" in capsys.readouterr().err


class TestConstruction:
    def test_cannot_be_built_empty(self):
        with pytest.raises(ConfigError):
            Allowlist.of([])

    def test_a_workspace_alone_is_enough_to_start(self):
        assert Allowlist.of([], workspaces=["jantrik"]).permits(
            Repository("jantrik", "admin-client")
        )


class TestWorkspaces:
    """Only code search asks this question, and only because Bitbucket's search endpoint
    is workspace-scoped. Reaching a workspace is not permission to read what it returns."""

    def test_it_names_the_workspaces_its_repositories_live_in(self):
        allowlist = Allowlist.of(
            [Repository("streamstech", "db-explorer"), Repository("jantrik", "admin-client")]
        )

        assert allowlist.workspaces() == frozenset({"streamstech", "jantrik"})

    def test_a_workspace_holding_an_allowlisted_repository_is_reachable(self):
        allowlist = Allowlist.of([Repository("streamstech", "db-explorer")])

        assert allowlist.permits_workspace("streamstech")
        assert allowlist.permits_workspace("STREAMSTECH"), "workspace names are not case-sensitive"

    def test_any_other_workspace_is_not(self):
        allowlist = Allowlist.of([Repository("streamstech", "db-explorer")])

        assert not allowlist.permits_workspace("jantrik")

    def test_reaching_the_workspace_is_not_reaching_its_other_repositories(self):
        allowlist = Allowlist.of([Repository("streamstech", "db-explorer")])

        assert allowlist.permits_workspace("streamstech")
        assert not allowlist.permits(Repository("streamstech", "secret-payroll"))

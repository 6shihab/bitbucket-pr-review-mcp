"""The Allowlisted Repository set, and the server's refusal to run without one.

ADR-0002 leans on this list: once browser-era credentials reach every repository the
Reviewer can, the allowlist is what bounds the damage a confused or injected Caller can
do. A wildcard would quietly restore "every repository I can reach" as the default, so
there is no way to write one.
"""

import pytest

from bitbucket_pr_review_mcp.references import Repository
from bitbucket_pr_review_mcp.settings import Allowlist, ConfigError, load_allowlist


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

    @pytest.mark.parametrize("wildcard", ["*", "*/*", "streamstech/*", "*/db-explorer"])
    def test_refuses_a_wildcard(self, tmp_path, wildcard):
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


class TestConstruction:
    def test_cannot_be_built_empty(self):
        with pytest.raises(ConfigError):
            Allowlist.of([])

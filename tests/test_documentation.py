"""The documentation, checked against the thing it documents.

Prose rots quietly. A tool renamed in `server.py` and left stale in the README is a
teammate's wasted afternoon, and the architecture document is worth nothing if it
describes a shape the code no longer has. These are the claims that can be checked
mechanically; the rest is the reader's job.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from bitbucket_pr_review_mcp.gate import CredentialGate
from bitbucket_pr_review_mcp.scopes import REQUIRED
from bitbucket_pr_review_mcp.server import build_server
from bitbucket_pr_review_mcp.settings import Settings

ROOT = Path(__file__).resolve().parents[1]
README = (ROOT / "README.md").read_text(encoding="utf-8")
ARCHITECTURE = (ROOT / "docs" / "architecture.md").read_text(encoding="utf-8")
ADRS = sorted((ROOT / "docs" / "adr").glob("*.md"))
DEPLOYING = (ROOT / "docs" / "deploying-the-shared-server.md").read_text(encoding="utf-8")


def flat(prose: str) -> str:
    """Collapse wrapping, so a phrase test is about the words rather than the column."""
    return " ".join(prose.split())


FLAT_README = flat(README)
FLAT_ARCHITECTURE = flat(ARCHITECTURE)


@pytest.fixture
def tool_names(wire, allowlist, keychain, setup_listener):
    async def names():
        server = build_server(Settings(), allowlist, CredentialGate(keychain, setup_listener))
        return [tool.name for tool in await server.list_tools()]

    return names


@pytest.fixture
def prompt_names(wire, allowlist, keychain, setup_listener):
    async def names():
        server = build_server(Settings(), allowlist, CredentialGate(keychain, setup_listener))
        return [prompt.name for prompt in await server.list_prompts()]

    return names


class TestTheToolTable:
    async def test_every_registered_tool_is_documented(self, tool_names):
        undocumented = [name for name in await tool_names() if name not in README]

        assert undocumented == []

    async def test_the_readme_invents_no_tools(self, tool_names):
        registered = set(await tool_names())
        claimed = set(re.findall(r"`(bitbucket_[a-z_]+)`", README))

        assert claimed - registered == set()

    async def test_there_are_the_eleven_adr_0005_agreed_on(self, tool_names):
        assert len(await tool_names()) == 11


class TestThePrompt:
    """ADR-0009's prompt is documented like a tool, because a caller meets it like one."""

    async def test_every_registered_prompt_is_documented(self, prompt_names):
        undocumented = [name for name in await prompt_names() if name not in README]

        assert undocumented == []

    def test_the_readme_says_the_user_chooses_what_is_posted(self):
        assert "Nothing is posted until you say so" in README
        assert "not approval to post" in FLAT_README

    def test_the_readme_says_it_is_text_rather_than_a_model(self):
        assert "It is text, not a model" in FLAT_README
        assert "0009-the-server-ships-the-review-prompt.md" in README


class TestTheSettingsTable:
    def test_every_setting_is_documented(self):
        undocumented = [
            name
            for name in Settings.model_fields
            if f"BB_MCP_{name.upper()}" not in README
        ]

        assert undocumented == []

    def test_the_documented_defaults_are_the_real_ones(self):
        settings = Settings()

        assert f"| `BB_MCP_LOG_LEVEL` | `{settings.log_level}` |" in README
        assert f"| `BB_MCP_MAX_COMMENTS` | `{settings.max_comments}` |" in README


class TestTheAdrs:
    def test_every_adr_is_referenced_from_the_architecture_document(self):
        unreferenced = [adr.name for adr in ADRS if adr.name not in ARCHITECTURE]

        assert unreferenced == []

    def test_every_link_from_the_architecture_document_resolves(self):
        links = re.findall(r"\]\((\.\./?[^)#]+)", ARCHITECTURE)
        missing = [link for link in links if not (ROOT / "docs" / link).resolve().exists()]

        assert missing == []

    def test_the_architecture_document_is_linked_from_the_readme(self):
        assert "docs/architecture.md" in README


class TestTheHonestClaim:
    """The one thing this documentation must not soften: the credential can merge."""

    def test_the_architecture_document_says_bitbucket_will_not_enforce_the_ceiling(self):
        assert "no permission that separates commenting from merging" in FLAT_ARCHITECTURE
        assert "does not claim it" in FLAT_ARCHITECTURE

    def test_it_names_all_four_mechanisms_including_the_one_we_cannot_impose(self):
        for mechanism in ["No such tool exists", "chokepoint", "Nothing deletes",
                          "Branch restrictions"]:
            assert mechanism in FLAT_ARCHITECTURE

    def test_the_readme_makes_branch_restrictions_a_prerequisite(self):
        assert "Before you start" in README
        assert "Branch restrictions" in README
        assert README.index("Branch restrictions") < README.index("## Install")


class TestTheSetupInstructions:
    def test_all_four_scopes_are_named(self):
        for scope in REQUIRED:
            assert scope in README

    def test_the_identifier_is_named_as_the_atlassian_account_email(self):
        assert "Atlassian account email" in FLAT_README
        assert "not your Bitbucket username" in FLAT_README

    def test_the_install_commands_are_the_same_on_every_platform(self):
        assert "uv sync" in README
        assert "Windows, macOS and Linux" in README


class TestTheAllowlistExample:
    EXAMPLE = ROOT / "config" / "repositories.yaml.example"

    def test_it_is_committed(self):
        assert self.EXAMPLE.exists()

    def test_it_holds_no_secrets(self):
        """Not "the word secret never appears" — it appears in the warning not to put one
        here. Nothing that looks like a credential being *assigned* a value."""
        body = self.EXAMPLE.read_text(encoding="utf-8").lower()

        assert not re.search(r"^\s*(token|password|secret|api_key)\s*:", body, re.MULTILINE)
        assert "atatt" not in body, "an Atlassian API token"

    def test_it_parses_as_the_allowlist_it_claims_to_be(self):
        from bitbucket_pr_review_mcp.settings import load_allowlist

        assert load_allowlist(self.EXAMPLE).names()


class TestTheFactsWorthKeeping:
    """Each of these was expensive to find, and three of them were found by being wrong."""

    @pytest.mark.parametrize(
        "fact",
        [
            "no `path` parameter",
            "answer with a 302",
            "abbreviated to twelve characters",
            "carries no repository field",
            "Granular scopes do not nest",
            "read:user:bitbucket",
            "start_from` and `start_to",
            "Deletion is a tombstone",
            "escapes HTML",
            "cannot be defined under `from __future__ import annotations`",
            "does not support PKCE",
            "app passwords were removed",
            "does not read `WWW-Authenticate` off a 200",
            "Keycloak does not implement RFC 8707",
            "replaces the built-in set",
            "granted whether or not it was requested",
        ],
    )
    def test_the_fact_is_recorded(self, fact):
        assert fact in FLAT_ARCHITECTURE


class TestTheContainer:
    """Docker collides with ADR-0003, so the documentation has to say how, not gloss it."""

    DOCKERFILE = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    COMPOSE = (ROOT / "docker-compose.yaml").read_text(encoding="utf-8")
    IGNORE = (ROOT / ".dockerignore").read_text(encoding="utf-8")
    GITIGNORE = (ROOT / ".gitignore").read_text(encoding="utf-8")

    def test_the_readme_says_a_container_has_no_keychain(self):
        assert "A container has no keychain" in FLAT_README
        assert "0007-the-container-is-given-its-credential.md" in README

    def test_it_names_the_cost_rather_than_glossing_it(self):
        assert "docker inspect" in FLAT_README

    def test_the_credential_file_is_never_committed(self):
        assert ".env.docker" in self.GITIGNORE
        assert ".env" in self.IGNORE

    def test_the_allowlist_is_not_baked_into_the_image(self):
        assert "config/repositories.yaml" in self.IGNORE
        assert "/config/repositories.yaml:ro" in self.COMPOSE

    def test_the_image_runs_as_a_person_who_is_not_root(self):
        assert "USER reviewer" in self.DOCKERFILE
        assert "read_only: true" in self.COMPOSE
        assert 'cap_drop: ["ALL"]' in self.COMPOSE

    def test_nothing_in_the_image_is_a_secret(self):
        for word in ("ATATT", "BB_MCP_API_TOKEN=", "BB_MCP_EMAIL="):
            assert word not in self.DOCKERFILE
            assert word not in self.COMPOSE

    def test_the_tests_are_not_shipped_in_the_image(self):
        assert "tests" in self.IGNORE


class TestTheCredentialLifecycle:
    """First run, persistence, deletion, and the property that holds them together."""

    def test_removing_the_credential_is_documented(self):
        assert "--forget" in README
        assert "still exists at Atlassian" in FLAT_README

    def test_the_readme_says_why_deletion_is_not_a_tool(self):
        assert "no tool" in FLAT_README
        assert "talk a model into calling" in FLAT_README

    def test_it_states_that_the_model_never_sees_the_token(self):
        assert "The model never sees the token" in README
        assert "never in a tool's answer" in FLAT_README

    def test_it_distinguishes_the_setup_token_from_the_api_token(self):
        assert "a *different* single-use token" in FLAT_README


class TestTheDeploymentDocument:
    """An operator who has not read this should not be running the shared server, so the
    sentences that make that true are checked rather than hoped for."""

    FLAT = flat(DEPLOYING)

    def test_it_says_what_one_compromise_costs(self):
        assert "What one compromise costs" in DEPLOYING

    def test_it_names_branch_restrictions_as_what_survives(self):
        assert "branch restrictions" in self.FLAT.lower()
        assert "still works after this server is owned" in self.FLAT

    def test_it_says_every_credential_can_merge(self):
        assert "can merge pull requests" in self.FLAT
        assert "0002-comment-only-blast-radius.md" in DEPLOYING

    def test_it_says_the_key_must_not_sit_with_the_backups(self):
        assert "must not live where the database" in self.FLAT
        assert "back the key up separately" in self.FLAT.lower()

    def test_it_says_rotation_does_not_re_enrol_anybody(self):
        assert "nobody re-enrols" in self.FLAT

    def test_it_says_what_revoking_does_not_do(self):
        for elsewhere in ("Keycloak", "Atlassian"):
            assert elsewhere in DEPLOYING

    def test_it_admits_the_compose_file_is_development_configuration(self):
        assert "development configuration" in self.FLAT.lower()

    def test_the_readme_sends_people_here_before_they_deploy(self):
        assert "deploying-the-shared-server.md" in README

    def test_the_architecture_document_links_it(self):
        assert "deploying-the-shared-server.md" in ARCHITECTURE


class TestTheOperatorCommands:
    async def test_every_one_of_them_is_documented(self, tool_names):
        for command in ("--health", "--who", "--revoke", "--rotate-key"):
            assert command in README, command

    async def test_none_of_them_is_a_tool(self, tool_names):
        """A pull request description must not be able to talk a Caller into revoking a
        colleague, rotating a key, or asking who else is enrolled."""
        names = await tool_names()

        for word in ("health", "revoke", "rotate", "who", "enrol"):
            assert not any(word in name for name in names), word

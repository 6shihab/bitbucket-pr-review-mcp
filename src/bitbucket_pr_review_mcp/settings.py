"""Configuration: server settings and the Allowlisted Repository set.

Split by how secret each piece is, following the sibling repository:

  * environment / .env        — tuning knobs                    (gitignored)
  * config/repositories.yaml  — the allowlist, no secrets       (committable)
  * the OS keychain           — the credential                  (never a file)

Nothing here imports FastMCP or httpx. Configuration is plain data, so it stays
testable without standing up a protocol or a network.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from pydantic_settings import BaseSettings, SettingsConfigDict

from .references import InvalidReference, Repository

ENTRY_FORMAT = "Each entry is 'workspace/repo', e.g. 'streamstech/db-explorer'."


class ConfigError(RuntimeError):
    """Raised when configuration is broken badly enough that we should not start."""


@dataclass(frozen=True, slots=True)
class Allowlist:
    """The repositories this server may touch at all.

    Membership is configured, never inferred from what the credential happens to reach.
    Bitbucket sells no permission that separates commenting from merging (ADR-0002), so
    this list is doing more work than it looks like it is.
    """

    _permitted: frozenset[str]
    _whole_workspaces: frozenset[str]
    _names: tuple[str, ...]

    @classmethod
    def of(
        cls, repositories: list[Repository], workspaces: list[str] | None = None
    ) -> Allowlist:
        """`repositories` are named one by one; `workspaces` are admitted entire.

        A whole workspace is the widest thing this list can say, and it is still a
        different statement from having no list: it names a workspace somebody chose,
        rather than deferring to whatever the credential turns out to reach.
        """
        whole = frozenset(workspace.strip().lower() for workspace in workspaces or [])
        named = frozenset(
            repo.full_name.lower()
            for repo in repositories
            if repo.workspace.lower() not in whole
        )

        if not named and not whole:
            raise ConfigError(
                "The repository allowlist is empty, so this server would be able to reach "
                f"nothing — and an absent list would let it reach everything. {ENTRY_FORMAT}"
            )

        return cls(
            _permitted=named,
            _whole_workspaces=whole,
            _names=tuple(sorted(named | {f"{workspace}/*" for workspace in whole})),
        )

    def permits(self, repository: Repository) -> bool:
        return (
            repository.workspace.lower() in self._whole_workspaces
            or repository.full_name.lower() in self._permitted
        )

    def permits_workspace(self, workspace: str) -> bool:
        """Whether any allowlisted repository lives in this workspace.

        Only code search needs this: Bitbucket's search endpoint is workspace-scoped, so
        the request cannot be narrower than a workspace even though the answer must be
        (ADR-0006). Permission to *ask* a workspace is not permission to read what comes
        back — `search.py` filters the results against `permits` before returning them.
        """
        return workspace.strip().lower() in self.workspaces()

    def workspaces(self) -> frozenset[str]:
        return self._whole_workspaces | frozenset(
            name.split("/", 1)[0] for name in self._permitted
        )

    def names(self) -> tuple[str, ...]:
        return self._names

    def whole_workspaces(self) -> tuple[str, ...]:
        """The workspaces admitted entire, for whoever has to say so out loud."""
        return tuple(sorted(self._whole_workspaces))


def load_allowlist(path: Path) -> Allowlist:
    """Read the allowlist, or refuse to start with a message a human can act on."""
    if not path.exists():
        raise ConfigError(
            f"No repository allowlist at {path}. This server will not run without one: "
            "an absent list is indistinguishable from permission to touch every "
            f"repository the credential can reach. {ENTRY_FORMAT}"
        )

    try:
        document: Any = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ConfigError(f"{path} is not readable as YAML: {exc}") from exc

    entries = (document or {}).get("repositories") if isinstance(document, dict) else None
    if not entries:
        raise ConfigError(
            f"{path} lists no repositories under a 'repositories:' key. {ENTRY_FORMAT}"
        )
    if not isinstance(entries, list):
        raise ConfigError(f"'repositories:' in {path} must be a list. {ENTRY_FORMAT}")

    read = [_read_entry(entry, path) for entry in entries]

    return Allowlist.of(
        repositories=[entry for entry in read if isinstance(entry, Repository)],
        workspaces=[entry for entry in read if isinstance(entry, str)],
    )


def _read_entry(entry: Any, path: Path) -> Repository | str:
    """One line of the allowlist: a repository, or a workspace name meaning all of it."""
    if not isinstance(entry, str):
        raise ConfigError(f"{entry!r} in {path} is not a repository name. {ENTRY_FORMAT}")

    workspace, _, repo = entry.strip().partition("/")

    if repo == "*" and "*" not in workspace:
        return _read_workspace(workspace, entry, path)

    if "*" in entry:
        raise ConfigError(
            f"{entry!r} in {path} is a wildcard. Only a whole named workspace can be "
            "written that way — 'workspace/*'. Anything else is a pattern, and a pattern "
            "is how 'every repository I can reach' becomes the accidental default. "
            f"{ENTRY_FORMAT}"
        )

    try:
        return Repository.parse(entry)
    except InvalidReference as exc:
        raise ConfigError(f"{entry!r} in {path} is not a repository. {ENTRY_FORMAT}") from exc


def _read_workspace(workspace: str, entry: str, path: Path) -> str:
    """`workspace/*`, checked as a workspace rather than taken on trust.

    `Repository.parse` is what knows a slug from a typo, so the name is put through it
    against a placeholder rather than being accepted because it sits before a star.
    """
    try:
        return Repository.parse(f"{workspace}/placeholder").workspace
    except InvalidReference as exc:
        raise ConfigError(
            f"{entry!r} in {path} does not name a workspace. {ENTRY_FORMAT}"
        ) from exc


class Settings(BaseSettings):
    """Server settings, read from the environment or a .env file."""

    model_config = SettingsConfigDict(env_prefix="BB_MCP_", env_file=".env", extra="ignore")

    repositories_file: Path = Path("config/repositories.yaml")
    log_level: str = "INFO"
    # One JSON object per line, for a log aggregator. Off by default because a
    # person reading a terminal is the commoner case, and JSON is unreadable there.
    log_json: bool = False
    request_timeout_seconds: float = 30.0

    # Response ceilings. A Caller reading past these is reviewing from memory, so both
    # are stated in the response rather than silently applied (ADR-0005's markdown note).
    max_changed_files: int = 300
    max_diff_characters: int = 60_000
    max_file_characters: int = 40_000
    max_directory_entries: int = 200
    max_commits: int = 50
    max_search_results: int = 25
    max_comments: int = 200

    # The shared deployment (tickets 10-16). Both are empty on a per-device install, and
    # nothing here refuses to start without them: stdio has one caller and needs neither.
    # `discovery.ProtectedResource` refuses the empty string, so the HTTP transport fails
    # to build rather than serving a document that describes nothing.
    public_url: str = ""
    oidc_issuer: str = ""
    vault_file: Path = Path("config/credentials.sqlite3")
    oidc_client_id: str = "bitbucket-pr-review-web"
    oidc_client_secret: str = ""

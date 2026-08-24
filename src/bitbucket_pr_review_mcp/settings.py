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
    _names: tuple[str, ...]

    @classmethod
    def of(cls, repositories: list[Repository]) -> Allowlist:
        if not repositories:
            raise ConfigError(
                "The repository allowlist is empty, so this server would be able to reach "
                f"nothing — and an absent list would let it reach everything. {ENTRY_FORMAT}"
            )
        names = tuple(sorted({repo.full_name.lower() for repo in repositories}))
        return cls(_permitted=frozenset(names), _names=names)

    def permits(self, repository: Repository) -> bool:
        return repository.full_name.lower() in self._permitted

    def names(self) -> tuple[str, ...]:
        return self._names


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

    return Allowlist.of([_read_entry(entry, path) for entry in entries])


def _read_entry(entry: Any, path: Path) -> Repository:
    if not isinstance(entry, str):
        raise ConfigError(f"{entry!r} in {path} is not a repository name. {ENTRY_FORMAT}")

    if "*" in entry:
        raise ConfigError(
            f"{entry!r} in {path} is a wildcard. The allowlist has to be enumerated: a "
            "pattern is how 'every repository I can reach' becomes the accidental "
            f"default. {ENTRY_FORMAT}"
        )

    try:
        return Repository.parse(entry)
    except InvalidReference as exc:
        raise ConfigError(f"{entry!r} in {path} is not a repository. {ENTRY_FORMAT}") from exc


class Settings(BaseSettings):
    """Server settings, read from the environment or a .env file."""

    model_config = SettingsConfigDict(env_prefix="BB_MCP_", env_file=".env", extra="ignore")

    repositories_file: Path = Path("config/repositories.yaml")
    log_level: str = "INFO"
    request_timeout_seconds: float = 30.0

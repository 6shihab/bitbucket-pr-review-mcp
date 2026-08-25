"""What this server tells Claude about who may authorise it.

Two documents and one header, all of them small, and all of them easy to get subtly
wrong in ways that produce "Couldn't reach the MCP server" and nothing else to go on.
The rules encoded here come from RFC 9728 and from Anthropic's connector documentation,
which is stricter than the RFC in two places worth knowing:

* The `resource` value must equal the MCP URL **exactly as the Owner types it into
  Claude**, path included. Not a normalised form of it, not the origin.
* The challenge must arrive on a **401**. Claude does not read `WWW-Authenticate` off a
  200, so answering a missing credential with a polite tool result means the connector
  never discovers where to send anybody.

`offline_access` is deliberately absent from `scopes_supported`. It is how a client asks
for a refresh token, not something this resource requires, and the MCP specification says
resource servers should not advertise it.

This module is plain data. It knows nothing of HTTP, MCP or the vault.
"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlsplit

WELL_KNOWN = "/.well-known/oauth-protected-resource"

# What a caller must hold to use this server at all. One scope: there is no useful
# narrower permission, because Bitbucket sells none (ADR-0002) and a token that can
# comment can merge. Pretending otherwise with a scope named "read" would be a lie told
# in a consent screen.
REVIEW_SCOPE = "bitbucket:review"

LOOPBACK = ("localhost", "127.0.0.1", "::1")


class DiscoveryError(RuntimeError):
    """The deployment is described in a way Claude will not accept."""


@dataclass(frozen=True, slots=True)
class ProtectedResource:
    """This server, as an OAuth protected resource."""

    resource: str
    authorization_servers: tuple[str, ...]
    scopes: tuple[str, ...] = (REVIEW_SCOPE,)

    @classmethod
    def of(
        cls, resource: str, issuer: str, *, scopes: tuple[str, ...] = (REVIEW_SCOPE,)
    ) -> ProtectedResource:
        return cls(
            resource=_canonical(resource, "the public URL of this server"),
            authorization_servers=(_canonical(issuer, "the authorization server issuer"),),
            scopes=scopes,
        )

    def document(self) -> dict[str, object]:
        """The body of `/.well-known/oauth-protected-resource`."""
        return {
            "resource": self.resource,
            "authorization_servers": list(self.authorization_servers),
            "scopes_supported": list(self.scopes),
            "bearer_methods_supported": ["header"],
        }

    def metadata_path(self) -> str:
        """Where that document is served.

        RFC 9728 puts the resource's path *after* the well-known segment, and Claude
        probes that form first. A resource with no path gets the bare well-known path.
        """
        return WELL_KNOWN + urlsplit(self.resource).path.rstrip("/")

    def metadata_url(self) -> str:
        split = urlsplit(self.resource)
        return f"{split.scheme}://{split.netloc}{self.metadata_path()}"

    def challenge(self, *, scope: str | None = None, error: str | None = None) -> str:
        """The `WWW-Authenticate` value. Belongs on a 401, or a 403 for a scope error."""
        parts = []
        if error:
            parts.append(f'error="{error}"')
        parts.append(f'resource_metadata="{self.metadata_url()}"')
        parts.append(f'scope="{scope or " ".join(self.scopes)}"')
        return "Bearer " + ", ".join(parts)


def _canonical(raw: str, what: str) -> str:
    """Refuse anything Claude would refuse, here rather than at connection time."""
    value = (raw or "").strip()
    if not value:
        raise DiscoveryError(
            f"{what} is not set, and this server cannot describe itself without it."
        )

    split = urlsplit(value)
    if not split.scheme or not split.netloc:
        raise DiscoveryError(
            f"{what} is {value!r}, which is not an absolute URL. It needs a scheme and a "
            "host, and it must match what is typed into Claude exactly."
        )
    if split.fragment:
        raise DiscoveryError(f"{what} is {value!r}; a canonical resource URI has no fragment.")
    if split.query:
        raise DiscoveryError(f"{what} is {value!r}; a canonical resource URI has no query.")
    if split.scheme != "https" and split.hostname not in LOOPBACK:
        raise DiscoveryError(
            f"{what} is {value!r}. Anthropic's servers connect to this address over the "
            "internet, so it has to be https — http is only accepted on loopback, for "
            "tests and local development."
        )
    return value.rstrip("/") if split.path in ("", "/") else value


__all__ = ["REVIEW_SCOPE", "WELL_KNOWN", "DiscoveryError", "ProtectedResource"]

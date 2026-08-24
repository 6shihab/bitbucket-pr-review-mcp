# 09 — Architecture doc and packaging

**What to build:** The documentation a new teammate reads before touching anything, in the
register the sibling repository set — prose that explains the shape and, more usefully, why
it is that shape. Someone who reads only the architecture document should be able to
navigate the whole repository and should understand what actually protects them.

The load-bearing part is honesty about containment. Bitbucket sells no permission that
separates commenting from merging, so any claim that the credential physically cannot merge
would be false. The document must say so plainly and then explain the three mechanisms that
do the work, including the one this code cannot impose: branch restrictions on the
repository, which belong in the README as a prerequisite a Reviewer sets up themselves.

Also: a README covering first-run setup and the credential flow, a committable allowlist
configuration example, and packaging that installs and runs on Windows, macOS and Linux
from the same instructions.

**Blocked by:** 02, 04, 07, 08 — documents the finished system.

**Status:** done

- [x] The architecture document explains the one-way import layering and why it exists
- [x] It states plainly that Bitbucket offers no comment-only permission, and explains the three mechanisms that bound writes instead
      Four, in the end. The third — that nothing deletes — had been folded into the
      chokepoint, but it fails differently from the other two and is the reason a
      half-posted batch is never rolled back, so it is named on its own.
- [x] The README documents branch restrictions as a prerequisite the Reviewer must configure
      Before the install instructions, not after: a prerequisite read after setup is a
      postscript.
- [x] The README covers first-run credential setup, including which scopes to grant and that the identifier is the Atlassian account email
- [x] A committable allowlist configuration example is included, with no secrets in it
- [x] The facts established during design are recorded so they are not re-derived: no path parameter on the diff endpoint, workspace-scoped code search with a substring repository filter, no PKCE, app passwords removed July 2026
      Eleven facts, including the five that were only found by running against the real
      API — three of which were wrong in the first implementation.
- [x] Install and run instructions work unchanged on Windows, macOS and Linux
      Verified on Windows: `uv sync`, `uv run bb-pr-mcp --version`, `--check` (exit 0),
      and a real MCP handshake over stdio listing all eleven tools. The commands contain
      nothing platform-specific; the README notes the one place a path differs.
- [x] Each ADR is referenced from the architecture document where the decision shows up in the code

## Notes

**The documentation is tested.** `tests/test_documentation.py` checks the claims that can
be checked mechanically: every registered tool is in the README's table and the README
invents none, every setting is documented with its real default, every ADR is referenced,
every relative link resolves, the four scopes are named, branch restrictions come before
the install instructions, and the eleven hard-won facts are still recorded. Prose rots
quietly, and a tool renamed in `server.py` and left stale in the README is a teammate's
wasted afternoon.

**Two things were wrong and are now fixed.** ADR-0002's opening sentence said the server
"may create, update and delete comments" — which the rest of the same ADR forbids and the
code has never done. And the MCP handshake was reporting FastMCP's version, 3.4.7, as the
server's own; a client showing that is telling its user about a dependency they have never
heard of.

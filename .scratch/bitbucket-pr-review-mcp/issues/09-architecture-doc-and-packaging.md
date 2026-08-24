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

**Status:** ready-for-agent

- [ ] The architecture document explains the one-way import layering and why it exists
- [ ] It states plainly that Bitbucket offers no comment-only permission, and explains the three mechanisms that bound writes instead
- [ ] The README documents branch restrictions as a prerequisite the Reviewer must configure
- [ ] The README covers first-run credential setup, including which scopes to grant and that the identifier is the Atlassian account email
- [ ] A committable allowlist configuration example is included, with no secrets in it
- [ ] The facts established during design are recorded so they are not re-derived: no path parameter on the diff endpoint, workspace-scoped code search with a substring repository filter, no PKCE, app passwords removed July 2026
- [ ] Install and run instructions work unchanged on Windows, macOS and Linux
- [ ] Each ADR is referenced from the architecture document where the decision shows up in the code

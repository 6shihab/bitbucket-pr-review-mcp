# 01 — Read a Pull Request end to end

**What to build:** A Reviewer configures the server in an MCP client, points a Caller at a
Bitbucket Cloud Pull Request by URL, and gets back its title, state, author, source and
destination branches, description, and Review Basis. That is the whole demoable
behaviour — but it drags the spine of the project with it, because none of the safety
properties can be bolted on afterwards.

The spine this ticket lays down: the project skeleton on the house stack (Python 3.13,
uv, FastMCP, pydantic-settings, loguru, ruff, pytest) with the one-way import layering
from the sibling repository, where configuration imports nothing, the Bitbucket client
knows nothing of MCP, and one module alone imports FastMCP. Settings carrying the
Allowlisted Repository set, which must be present and non-wildcard or the server refuses
to start. Pull Request reference parsing that accepts a full Bitbucket URL or the
workspace/repo/id shorthand and rejects anything else with an error naming both accepted
shapes. The HTTP client taking an injected transport, with the allowlist guard and the
write chokepoint from ADR-0002 already in place and already tested — no writes exist yet,
but the guard that will refuse them does. Markdown responses rather than JSON. Logging to
stderr, never stdout. A check flag that validates configuration and credential and exits
with a shell status.

The credential comes from an environment variable in this ticket. That is a deliberate
stopgap so a Reviewer can demo a working read before the credential system exists; ticket
02 replaces it with the keychain.

**Blocked by:** None — can start immediately.

**Status:** done

- [x] `bitbucket_get_pull_request` accepts a full Bitbucket URL and returns title, state, author, both branch names, description and Review Basis
- [x] The same tool accepts the workspace/repo/id shorthand and resolves to the same Pull Request
- [x] A malformed reference is rejected with an error naming both accepted forms
- [x] The server refuses to start when the allowlist is missing, empty, or a wildcard
- [x] A request against a repository outside the allowlist is refused before any HTTP request is made
- [x] The write chokepoint refuses merge, approve, decline and repository-write URLs however they are constructed, and refuses DELETE anywhere
- [x] Every safety property above is exercised as a plain function call against an injected transport, with no MCP client and no network
- [x] Nothing is ever written to stdout except MCP protocol messages
- [x] The check flag reports configuration and credential status and exits with a meaningful shell status
- [x] Response fixtures are captured from real Bitbucket responses, not hand-authored
      **Met during ticket 03.** `tests/recorded/` holds a pull request, a diffstat and a
      diff captured from `jantrik/admin-client` pull request 2476 on 2026-08-24, and
      `test_recorded_responses.py` runs the production readers over them. The synthetic
      fixtures stay, because no single real pull request has a rename, a binary file and
      a lockfile in it — but their shapes are now checked rather than assumed.

      The capture corrected two assumptions immediately, which is the whole argument for
      doing it: `/diffstat` redirects (the client only followed redirects on `/diff`),
      and `source.commit.hash` is abbreviated to twelve characters.

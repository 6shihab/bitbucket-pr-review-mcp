# 04 — Read the repository around the change

**What to build:** A Caller reviewing a hunk needs to look past it — whether the function
being changed has three other callers, what the last commit to this file was trying to do,
whether the test that should have changed alongside it exists. This ticket gives it that
reach: repository metadata and default branch, file content at a ref, directory listings,
commit history for either a ref or a Pull Request, and code search across the repository.

Per ADR-0006 this is a deliberate widening — the Caller can now read any file in any
Allowlisted Repository, not only the files the Pull Request touched. A reviewer that cannot
look around is a linter, and the allowlist is what bounds the cost.

Code search needs its own guard. Bitbucket's search is workspace-scoped and its repository
filter is a substring inside a caller-supplied query string, so enforcing the allowlist by
composing that string would be enforcement by concatenation — defeated by a query that
adds, removes or duplicates a filter term. The tool therefore requires an explicit
repository argument and composes the filter itself, **and** filters the returned results by
repository before any of them reach the Caller.

**Blocked by:** 01 — needs the HTTP client, allowlist guard and reference parsing.

**Status:** done

- [x] Repository metadata returns the default branch
- [x] File content is returned at a named ref, including the Review Basis
- [x] Directory listing returns entries at a named ref
- [x] Commit history accepts exactly one of a ref or a Pull Request, and errors clearly when given both or neither
- [x] Code search requires an explicit repository argument and composes the repository filter itself
- [x] A result outside the allowlist planted in a search response is filtered out before it is returned
- [x] A request for a file or directory in a repository outside the allowlist is refused before any HTTP request is made
- [x] All returned content is wrapped in untrusted-content delimiters with the standing notice
- [x] Large listings and search results state truncation explicitly

## Notes

**The chokepoint was widened, deliberately.** `GET /2.0/workspaces/{ws}/search/code` is
the only endpoint this server touches that is not repository-scoped, so `guard.py` now
permits it — for a workspace the allowlist reaches, and nothing else under `/workspaces`.
That is a real widening of ADR-0002's mechanism 2 and it is tested as one.

**Search carries its own allowlist check, twice.** The guard can only see the workspace,
because the repository lives in a query parameter. So `search_code` checks the repository
against the allowlist before composing anything, refuses a query carrying its own `repo:`
(or `project:`, `workspace:`, `org:`, `user:`) term, and checks every returned result
against the allowlist before any of it reaches the Caller.

**A search result states its origin only in `file.links.self.href`.** There is no
repository field on a search result at all — found by running against a real workspace,
where the first version of the filter discarded every legitimate match. A result whose
origin cannot be read is discarded rather than assumed local: this is the backstop, and a
backstop that guesses is not one.

**`/commits` spells hashes in full; the pull request payload abbreviates.** Recorded in
`tests/recorded/commits.json` — another reason tickets 06 and 07 need a prefix-aware
Basis comparison.

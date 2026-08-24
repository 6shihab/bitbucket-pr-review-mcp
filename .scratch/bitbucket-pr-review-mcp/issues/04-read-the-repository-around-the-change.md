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

**Status:** ready-for-agent

- [ ] Repository metadata returns the default branch
- [ ] File content is returned at a named ref, including the Review Basis
- [ ] Directory listing returns entries at a named ref
- [ ] Commit history accepts exactly one of a ref or a Pull Request, and errors clearly when given both or neither
- [ ] Code search requires an explicit repository argument and composes the repository filter itself
- [ ] A result outside the allowlist planted in a search response is filtered out before it is returned
- [ ] A request for a file or directory in a repository outside the allowlist is refused before any HTTP request is made
- [ ] All returned content is wrapped in untrusted-content delimiters with the standing notice
- [ ] Large listings and search results state truncation explicitly

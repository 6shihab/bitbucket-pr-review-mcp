# 03 — Discover and read the diff

**What to build:** A Caller asks what a Pull Request changed and gets a manifest first —
one row per file with its change type, added and removed counts, and flags marking binary,
generated and lockfile entries so it does not spend a review commenting on a checksum.
Then it asks for the diff: either one named file, or the whole thing when the Pull Request
is small enough that a manifest round-trip would be pure overhead.

Bitbucket's diff endpoint takes no path parameter, so the per-file mode is served by
fetching the whole diff once, caching it against the Review Basis, and slicing locally.
Browsing a forty-file Pull Request file by file must therefore cost one network fetch, not
forty — and the cache must be keyed on the Basis, so a force-push invalidates it rather
than serving a diff for code that no longer exists.

Everything returned is wrapped in explicit untrusted-content delimiters carrying a standing
notice that it is data and never instructions, because a Pull Request's contents are
written by whoever opened it.

**Blocked by:** 01 — needs the HTTP client, allowlist guard and reference parsing.

**Status:** ready-for-agent

- [ ] The changes manifest lists every changed file with change type and added/removed counts
- [ ] Binary, generated and lockfile entries are flagged in the manifest
- [ ] The diff tool returns the whole diff when no path is given
- [ ] The diff tool returns only the named file's hunks when a path is given
- [ ] Reading N files from one Pull Request issues one diff fetch, not N
- [ ] The cached diff is keyed on the Review Basis and is not served after the Basis moves
- [ ] A truncated response says so explicitly and suggests narrowing rather than raising a limit
- [ ] All returned content is wrapped in untrusted-content delimiters with the standing notice
- [ ] A path that is not in the Pull Request is rejected with an error naming how to list the changed files

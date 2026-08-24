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

**Status:** done

- [x] The changes manifest lists every changed file with change type and added/removed counts
- [x] Binary, generated and lockfile entries are flagged in the manifest
- [x] The diff tool returns the whole diff when no path is given
- [x] The diff tool returns only the named file's hunks when a path is given
- [x] Reading N files from one Pull Request issues one diff fetch, not N
- [x] The cached diff is keyed on the Review Basis and is not served after the Basis moves
- [x] A truncated response says so explicitly and suggests narrowing rather than raising a limit
- [x] All returned content is wrapped in untrusted-content delimiters with the standing notice
- [x] A path that is not in the Pull Request is rejected with an error naming how to list the changed files

## Notes

**The diff endpoint redirects.** Bitbucket answers `/pullrequests/{id}/diff` with a 302 to
the commit-spec diff URL. The transport is deliberately built with `follow_redirects=False`
(ticket 01), so the hop is followed in `client.get_text` and the target goes back through
`assert_permitted` — a transport-level redirect would have skipped the chokepoint, which
is the one thing ADR-0002 cannot afford. A redirect off `api.bitbucket.org` is refused.

**Hunk ranges are parsed now, not in ticket 06.** Ticket 06 validates Anchors against the
hunks; reading `@@` headers here rather than re-parsing there keeps one parser.

**Classification is by path.** The diffstat endpoint carries no binary flag and no file
content, so binary/generated/lockfile are recognised from the path. The flags are
advisory — nothing is filtered out, and the manifest still lists every file.

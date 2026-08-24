# 05 — Read existing comments

**What to build:** Before a Caller writes anything, it needs to know what has already been
said — so it neither repeats a point a colleague made nor talks past an open thread. This
tool returns the Pull Request's existing comments with their author, their body, whether
they are Inline or Summary, the file and line each Inline Comment is anchored to, whether
that Anchor has gone orphaned, and whether the comment was written by this server's own
identity.

That last flag is what later tickets are built on: deduplication needs to recognise our own
comments, updating the Summary Comment must verify authorship by re-reading it rather than
trusting an id passed in, and stale comments of ours need surfacing so a new Review can
acknowledge them.

**Blocked by:** 01 — needs the HTTP client, allowlist guard and reference parsing.

**Status:** ready-for-agent

- [ ] Existing comments are returned with author, body, and Inline or Summary kind
- [ ] Inline Comments carry their file path and line
- [ ] An Anchor that Bitbucket reports as outdated is marked as orphaned
- [ ] Comments authored by this server's own identity are marked as such, determined from the comment's author rather than from its content
- [ ] Pagination is followed so a Pull Request with many comments returns all of them, or says explicitly that it truncated
- [ ] All returned content is wrapped in untrusted-content delimiters with the standing notice

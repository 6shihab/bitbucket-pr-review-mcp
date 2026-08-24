# 08 — The canonical Summary Comment

**What to build:** A Review gets one Summary Comment carrying its overall shape — what was
reviewed, what was found, tabulated by Severity — so a human reads the verdict before the
line-by-line detail. On the third review of the same Pull Request there is still exactly
one, updated in place, rather than five stale summaries stacked up the page.

The server recognises its own Summary Comment by a stable marker it embeds in the body when
posting. That is the only state this server keeps, and it deliberately lives in the comment
rather than in a database.

Updating is the only write besides posting that ADR-0002 permits, and it is permitted only
against a comment this server authored — verified by re-reading the comment's author from
Bitbucket, never by trusting the id the Caller passed. A mistaken argument must not be able
to overwrite a colleague's words.

**Blocked by:** 06 — needs Finding rendering and the Attribution Footer.

**Status:** done — every criterion verified live

- [x] A first review posts one Summary Comment carrying the Review as a whole
      Verified live: comment #847224255 on `jantrik/admin-client` 2476.
- [x] Findings are tabulated by Severity in the Summary
      Counted from the comments actually on the Pull Request, not from what the Caller
      says it posted — a summary that disagrees with the page it heads is worse than no
      summary. The live one counted 1 MEDIUM and 2 LOW, matching ticket 07's batch.
- [x] A second review updates that same comment in place rather than posting another
      Verified live, twice.
- [x] The server's own Summary Comment is recognised by its embedded marker after a restart
      The live check built a **fresh server** for the second pass, so nothing carried
      over but the marker in the comment body.
- [x] An update against a comment authored by anybody else is refused, with authorship verified by re-reading the comment
- [x] The Summary Comment carries the Attribution Footer
- [x] The write chokepoint permits this PUT and still refuses every other write shape, including DELETE

## Notes

**The marker is not an HTML comment, because Bitbucket escapes HTML rather than dropping
it.** The first live summary carried `<!-- bitbucket-pr-review-mcp:summary/1 -->` on its
own line, and the rendered body came back as
`<p>&lt;!-- bitbucket-pr-review-mcp:summary/1 --&gt;</p>` — visible angle brackets at the
top of the comment, which is precisely the stray-looking text a human tidies away. The
marker moved into the Attribution Footer, where it reads as a tool identifier. The two
formats share the token, so the already-posted summary was found and updated in place
rather than orphaned.

**Authorship is re-read, never assumed.** Passing a `comment_id` costs an extra GET
before the PUT, and a comment written by anyone else is refused with their name in the
message. A deleted comment is refused too: Bitbucket keeps the id long after the body is
gone, and writing into one would be a write into a grave.

**Without an identity there is no update at all.** A credential that cannot read
`/2.0/user` cannot tell our summary from anybody else's, so `publish_summary` refuses
before making any request rather than guessing.

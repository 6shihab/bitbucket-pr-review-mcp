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

**Status:** ready-for-agent

- [ ] A first review posts one Summary Comment carrying the Review as a whole
- [ ] Findings are tabulated by Severity in the Summary
- [ ] A second review updates that same comment in place rather than posting another
- [ ] The server's own Summary Comment is recognised by its embedded marker after a restart
- [ ] An update against a comment authored by anybody else is refused, with authorship verified by re-reading the comment
- [ ] The Summary Comment carries the Attribution Footer
- [ ] The write chokepoint permits this PUT and still refuses every other write shape, including DELETE

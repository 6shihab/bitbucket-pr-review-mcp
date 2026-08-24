# 14 — Attribution when the server is shared

**What to build:** Every comment posts under the Bitbucket account of the person whose
session asked for it, and the Attribution Footer names them. A shared server must never
attribute one person's review to another.

Ticket 05 established that "is this comment ours?" is answered from the author's account
id, cached per process by `KnownIdentity`. On a shared server that cache is a bug with a
name: two people, one cached identity, and a re-review that either stacks duplicates or —
much worse — updates a summary comment belonging to somebody else.

**Blocked by:** 12.

**Status:** ready-for-agent

- [ ] `KnownIdentity` is per person, and one session cannot read another's
- [ ] The Attribution Footer names the Reviewer whose credential posted the comment
- [ ] "Ours" means "this person's", not "this server's", everywhere it is decided
- [ ] The canonical Summary Comment is found and updated per person, and one person can
      never update another's — authorship re-read, as ticket 08 does it
- [ ] Two people reviewing the same pull request at once produce two correct sets of
      comments, under a test that runs them concurrently
- [ ] A person's identity is forgotten when their credential is removed or their enrolment
      revoked

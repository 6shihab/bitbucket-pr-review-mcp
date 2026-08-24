# The server can only comment — and Bitbucket will not help us enforce it

This server writes to production pull requests, so its ceiling is drawn deliberately:
it may create and update comments and nothing else. No approve, no
request-changes, no merge, no decline, no pull request create or edit, no branch or
file writes, no repository administration.

**Bitbucket cannot enforce that ceiling for us, and it is worth being blunt about
why.** `write:pullrequest:bitbucket` grants create, update, comment, approve, decline
*and merge* as one indivisible unit. There is no comment-only scope, no token type
that has one, and no repository permission level that has one — in Bitbucket Cloud,
commenting and merging sit at the identical permission level. Any design claiming "the
credential physically cannot merge" is wrong. Ours does not claim it.

So the ceiling rests on three mechanisms that fail differently:

1. **No such tool exists.** Nothing in the tool surface can approve, decline or merge.
2. **One chokepoint in the HTTP client.** Every outbound request passes a single
   guard: GET is permitted anywhere under an allowlisted repository; the only writes
   permitted at all are POST to `.../pullrequests/{id}/comments` and PUT to
   `.../comments/{id}`. DELETE is not on the list — nothing in this server ever
   removes a comment. This catches what mechanism 1 cannot — a future tool that
   forgets the rule, a bug in URL construction, a refactor that widens something by
   accident. It does not care how the request was formed.
3. **Branch restrictions on the repository itself.** Restricting who may merge to the
   default branch, and requiring approvals, is enforced by Bitbucket rather than by
   this code, so it survives every mistake made in this repository. It is a
   prerequisite we can document and check but cannot impose.

A configured repository allowlist bounds all of this further; the server refuses to
start with an empty or wildcard allowlist.

The single PUT exists for one purpose: keeping one canonical Summary Comment current
across repeated reviews instead of stacking a new one each time. It is permitted only
against a comment authored by this server's own identity, verified by re-reading the
comment's author — never trusted from the caller's argument.

Stale Inline Comments are left standing. A comment whose Anchor has gone orphaned may
already carry a human reply, and destroying a conversation to tidy a list is a worse
outcome than an outdated note.

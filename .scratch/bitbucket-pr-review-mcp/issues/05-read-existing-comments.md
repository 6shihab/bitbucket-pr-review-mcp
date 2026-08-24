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

**Status:** done — one criterion verified against fixtures only, noted below

- [x] Existing comments are returned with author, body, and Inline or Summary kind
- [x] Inline Comments carry their file path and line
- [x] An Anchor that Bitbucket reports as outdated is marked as orphaned
      Two signals, not one: Bitbucket's `inline.outdated` when it is present, and an
      anchor with neither an old nor a new line when it is not. Missing an orphan means
      a Caller replies to a comment about code that is no longer there.
      **Not yet seen on a live pull request** — the test pull request has no comments, so
      this rests on the documented shape. The first real orphaned comment should be
      recorded into `tests/recorded/` and this assumption checked.
- [x] Comments authored by this server's own identity are marked as such, determined from the comment's author rather than from its content
- [x] Pagination is followed so a Pull Request with many comments returns all of them, or says explicitly that it truncated
- [x] All returned content is wrapped in untrusted-content delimiters with the standing notice

## Notes

**Ownership is computed from the account id, and the response says so.** The listing is
untrusted content, so a body can forge a `--- comment #7 ... [ours] ---` delimiter line
inside the fence. The guidance above the fence states that the flags were computed by
this server from author accounts, not read from the text. Ticket 08's authorship
re-check is the real defence; this is the cheap one.

**Unknown ownership is reported, never guessed.** A credential that cannot read
`/2.0/user` yields `is_ours = None` for every comment and a notice saying not to
deduplicate or update against the listing. Guessing "not ours" would stack duplicate
summaries; guessing "ours" would edit somebody else's comment.

**`KnownIdentity` caches `/2.0/user` for the life of the process** — otherwise every
comment read costs a round trip to ask a question whose answer cannot change while the
credential does not. A refusal is cached too, so a token missing `read:user:bitbucket`
does not re-ask on every call. Completing setup clears it, because a new credential is a
new account: `CredentialGate.when_credential_changes` exists for exactly that.

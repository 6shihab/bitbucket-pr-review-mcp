# 07 — Batch posting and re-review hygiene

**What to build:** A Caller posts a whole Review in one call rather than a dozen. The point
is not saving round-trips — it is that the batch either validates or fails before any of it
reaches the Pull Request: every Anchor checked against the hunks and the Review Basis
re-checked up front, so a partial write can only ever come from the network, never from bad
input.

When the network does fail halfway, the call returns a per-comment result saying exactly
what landed, and **does not roll back**. Deleting comments a human may already have read is
worse than an honest partial report, which is also why ADR-0002 permits no DELETE anywhere
in this server.

This ticket also makes the third review of a Pull Request behave. An Inline Comment
identical to one already at that path and line is refused rather than duplicated. Comments
of ours whose Anchors have gone stale are surfaced and marked so the Caller can acknowledge
them in the new Summary — and left standing, never tidied away, because a stale comment may
already have a colleague's reply hanging off it and destroying a conversation to shorten a
list is the worse outcome.

**Blocked by:** 06 — needs Anchor validation, the Basis check and Finding rendering.

**Status:** done

- [x] Many comments post in one call, each landing on its correct file and line
      Verified live: three findings posted to `jantrik/admin-client` 2476 in one call,
      and Bitbucket stored `{"to": 2}`, `{"from": 2}` and `{"start_to": 3, "to": 5}`
      respectively. The middle one is the inversion trap watched happening: `added 2`
      and `removed 2` are two different places, and both landed on the right one.
- [x] One invalid Anchor anywhere in the batch prevents the entire batch from posting
      Verified live against `jantrik/admin-client` 2476: a batch of two, one anchored to
      an invented line 400, refused with nothing posted and both problems listed.
- [x] A stale Review Basis refuses the entire batch
      Verified live: posting against the destination branch's commit was refused with
      both commits named.
- [x] A mid-batch network failure returns a per-comment result naming exactly what landed
- [x] Nothing is deleted or reverted after a partial failure
- [x] A single comment posts through the same tool without ceremony
- [x] An Inline Comment identical to an existing one at the same path and line is refused
      Verified live: re-posting ticket 06's comment was skipped, naming #847207531.
- [x] Stale Inline Comments authored by this server are surfaced and marked, and are not deleted

## Notes

**Validation is a phase, not a per-comment step.** The Basis is re-checked, then every
Anchor is checked, and only then does the first POST go out. That is what makes the
guarantee "a partial write can only come from the network" true rather than aspirational.

**Duplicate detection compares what was said, not who said it.** An identical point from
a colleague counts as already made: the reader cannot tell the difference, and repeating
it is the same noise either way. Comparison normalises whitespace and case and strips
this server's own furniture — the severity heading and the Attribution Footer — because
an existing comment of ours carries both and the Finding being posted carries neither
yet. Without that, every re-review would look like a new point.

**A rejected credential stops the batch; a single failed request does not.** A 401 will
not fix itself on the next comment, so the rest are marked `not attempted` rather than
hammered. A 500 might be one bad request, so the batch continues and the report says
which ones to re-post.

## Note from the first live run (2026-08-24)

**The Review Basis is abbreviated.** `source.commit.hash` on the pull request payload
comes back as twelve characters (`f90a2239dbc1`), while the same commit is spelled in
full inside `links` and in the diffstat entries. Comparing a stored Basis against a
freshly-read one with `==` will therefore refuse writes that should have been allowed,
or — worse, if the spellings are ever swapped — allow one it should have refused. The
comparison has to be prefix-aware in whichever direction is shorter, and the length has
to be long enough to mean something. `tests/test_recorded_responses.py` pins the
observed behaviour.

**What deletion looks like.** The two comments from ticket 06 were deleted by hand
between runs and came back in the listing with their ids intact and their bodies empty.
`Conversation.ours` now excludes them — ticket 08 finds the Summary Comment to update
through that property, and updating a deleted comment would be a write into a grave.
They stay listed and marked `deleted`, because a thread that existed is worth knowing
about, and they no longer block a re-post of the same point.

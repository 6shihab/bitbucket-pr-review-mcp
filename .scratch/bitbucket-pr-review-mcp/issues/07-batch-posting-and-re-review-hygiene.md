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

**Status:** ready-for-agent

- [ ] Many comments post in one call, each landing on its correct file and line
- [ ] One invalid Anchor anywhere in the batch prevents the entire batch from posting
- [ ] A stale Review Basis refuses the entire batch
- [ ] A mid-batch network failure returns a per-comment result naming exactly what landed
- [ ] Nothing is deleted or reverted after a partial failure
- [ ] A single comment posts through the same tool without ceremony
- [ ] An Inline Comment identical to an existing one at the same path and line is refused
- [ ] Stale Inline Comments authored by this server are surfaced and marked, and are not deleted

## Note from the first live run (2026-08-24)

**The Review Basis is abbreviated.** `source.commit.hash` on the pull request payload
comes back as twelve characters (`f90a2239dbc1`), while the same commit is spelled in
full inside `links` and in the diffstat entries. Comparing a stored Basis against a
freshly-read one with `==` will therefore refuse writes that should have been allowed,
or — worse, if the spellings are ever swapped — allow one it should have refused. The
comparison has to be prefix-aware in whichever direction is shorter, and the length has
to be long enough to mean something. `tests/test_recorded_responses.py` pins the
observed behaviour.

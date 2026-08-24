# 06 — Post one Inline Comment, correctly anchored

**What to build:** A Caller posts one Finding — a Severity, a category, a message and an
Anchor — and it appears on the Pull Request next to the code it is about, carrying an
Attribution Footer that names it machine-generated and names the Reviewer who ran it.

The Anchor is expressed as a path, a line, and a side of added, removed or context. The
server translates that to Bitbucket's old-line and new-line fields, because those raw
fields are the single easiest thing on this API to invert and inverting them attaches the
comment to the wrong code rather than raising an error. Before anything is sent, the Anchor
is validated against the parsed diff hunks; an invented line number is refused with an
error naming the nearest valid lines, so the Caller recovers on the next call instead of
guessing.

The Review Basis is required on the call and re-checked against the Pull Request's current
head. If the branch was force-pushed since the diff was read, the post is refused with an
instruction to re-fetch — better a clean retry than eight comments attached to code that no
longer exists. After posting, the Anchor is re-read and reported if it came back orphaned.

Severity reuses the house ladder — CRITICAL, HIGH, MEDIUM, LOW, with block, warn, info and
note semantics — rather than introducing a second vocabulary. The server has an opinion
about layout and none about correctness; that boundary is the whole of ADR-0001.

**Blocked by:** 03 (needs parsed diff hunks), 05 (needs comment reading for authorship).

**Status:** done — one criterion still unverified against a live pull request

- [x] A Finding posts as an Inline Comment on the correct file and line, verified by reading it back
      Read back from Bitbucket's own record of the created comment, which is what the
      POST returns. **Not yet done against a live pull request** — see below.
- [x] An Anchor on an added line, a removed line and a context line each land correctly
- [x] A multi-line range anchors to the whole block rather than one line within it
      As far as the API allows: Bitbucket's `inline` object has no range, so the comment
      attaches at the first line and the body names the block. Silently commenting on one
      line of five would misrepresent what the Finding is about.
- [x] An Anchor not present in the diff is refused before anything is posted, with the nearest valid lines named
- [x] A post whose Review Basis no longer matches the Pull Request head is refused with an instruction to re-fetch
- [x] A posted comment that comes back orphaned is reported rather than silently accepted
- [x] Every posted comment carries the Attribution Footer naming it machine-generated and naming the Reviewer
- [x] No argument suppresses the Attribution Footer
- [x] Severity and category render consistently across Findings
- [x] The write chokepoint permits this POST and still refuses every other write shape

## Notes

**The diff now carries an anchor gutter.** Ticket 03 rendered hunks verbatim, which left
the Caller to work out that the fourth `+` in a hunk is line 16 — two counters advancing
at different rates, which is exactly the arithmetic a language model gets wrong. The
numbers were already being computed to validate Anchors, so `diff_markdown` shows them:

    12    12 |      session = build_session()
    13       | -    return session.post(UPSTREAM, json=payload)
          13 | +    for attempt in range(RETRIES):

The legend outside the fence says which column each side anchors by, and that the gutter
is this server's while everything after the `|` is Bitbucket's.

**`inline.to` is the new file, `inline.from` is the old one**, and the translation lives
in exactly one function. There are tests asserting that the same number on two different
sides produces two different anchors, because inverting these does not raise — it
attaches a comment to real code that nobody is reviewing.

**Still to verify live:** posting to a real pull request. The read path has been exercised
against `jantrik/admin-client` 2476 throughout, but nothing has been written to it —
writing to somebody's repository is not something to do without being asked.

## Note from the first live run (2026-08-24)

**The Review Basis is abbreviated.** `source.commit.hash` on the pull request payload
comes back as twelve characters (`f90a2239dbc1`), while the same commit is spelled in
full inside `links` and in the diffstat entries. Comparing a stored Basis against a
freshly-read one with `==` will therefore refuse writes that should have been allowed,
or — worse, if the spellings are ever swapped — allow one it should have refused. The
comparison has to be prefix-aware in whichever direction is shorter, and the length has
to be long enough to mean something. `tests/test_recorded_responses.py` pins the
observed behaviour.

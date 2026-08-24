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

**Status:** ready-for-agent

- [ ] A Finding posts as an Inline Comment on the correct file and line, verified by reading it back
- [ ] An Anchor on an added line, a removed line and a context line each land correctly
- [ ] A multi-line range anchors to the whole block rather than one line within it
- [ ] An Anchor not present in the diff is refused before anything is posted, with the nearest valid lines named
- [ ] A post whose Review Basis no longer matches the Pull Request head is refused with an instruction to re-fetch
- [ ] A posted comment that comes back orphaned is reported rather than silently accepted
- [ ] Every posted comment carries the Attribution Footer naming it machine-generated and naming the Reviewer
- [ ] No argument suppresses the Attribution Footer
- [ ] Severity and category render consistently across Findings
- [ ] The write chokepoint permits this POST and still refuses every other write shape

## Note from the first live run (2026-08-24)

**The Review Basis is abbreviated.** `source.commit.hash` on the pull request payload
comes back as twelve characters (`f90a2239dbc1`), while the same commit is spelled in
full inside `links` and in the diffstat entries. Comparing a stored Basis against a
freshly-read one with `==` will therefore refuse writes that should have been allowed,
or — worse, if the spellings are ever swapped — allow one it should have refused. The
comparison has to be prefix-aware in whichever direction is shorter, and the length has
to be long enough to mean something. `tests/test_recorded_responses.py` pins the
observed behaviour.

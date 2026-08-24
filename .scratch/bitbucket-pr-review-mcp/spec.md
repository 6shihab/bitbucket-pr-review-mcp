# Bitbucket PR Review MCP

Status: ready-for-agent

## Problem Statement

A developer at StreamsTech wants a language model to review a Bitbucket Cloud Pull
Request and leave its comments where the team already reads them — anchored to the
lines they are about, plus one summary at the top.

Today that only works inside Claude Code, via a skill that shells out to curl and Git
Credential Manager. Everywhere else it does not work at all: Claude Desktop and the
OpenAI Agents SDK have no route to a private Bitbucket Pull Request, the GitHub CLI is
GitHub-only, and a plain web fetch returns 404. So the capability is trapped in one
client, re-improvised each session, and its safety properties are whatever the model
remembered that day.

There is a sharper worry underneath. Handing a model a credential that can write to
production Pull Requests is not a small thing, and Bitbucket does not sell the
permission that would make it safe by construction: the permission to comment is the
same permission as the permission to merge. A Reviewer needs to know exactly what the
thing can and cannot do, and needs that answer to survive a bug, a careless refactor,
and a hostile line of text inside a Pull Request description.

## Solution

An MCP server that speaks Bitbucket Cloud on the Reviewer's behalf, over stdio, on
their own machine. It offers eleven tools: nine that read a repository and its Pull
Requests, and two that post and update comments. The Caller does the reviewing; the
server fetches material, renders Findings, posts them, and refuses everything else.

The first time it runs with no credential, it opens a page on loopback and hands the
Reviewer a URL. They paste their Atlassian account email and an API token, the server
verifies both against Bitbucket and shows their display name back, stores the
credential in the OS keychain, and shuts the page down. Every session after that is
silent.

Reading follows discover-then-detail: a manifest of changed files first, then diffs per
file, with whole-repository tools available when a Finding needs checking against code
the Pull Request did not touch. Posting is guarded three ways — every Anchor is
validated against real diff hunks before anything is sent, the Review Basis is
re-checked so a force-push mid-review is refused rather than silently misplaced, and a
single chokepoint in the HTTP client permits only two write shapes in the whole server.

Every comment carries an Attribution Footer naming it as machine-generated and naming
the Reviewer who ran it. That is not a setting.

## User Stories

### Getting set up

1. As a Reviewer, I want the server to tell me it has no credential rather than failing
   with a 401, so that I know what to do about it.
2. As a Reviewer, I want a URL I can click the first time I use the server, so that I do
   not have to go hunting for Bitbucket's token page myself.
3. As a Reviewer, I want the setup page to tell me exactly which token scopes to tick, so
   that I grant neither more than the server needs nor less than it can use.
4. As a Reviewer, I want the setup page to ask for my Atlassian account email by that
   name, so that I do not enter my Bitbucket username and get an unexplained 401 three
   days later.
5. As a Reviewer, I want my credential checked against Bitbucket before it is saved, so
   that a typo fails while I am still looking at the form.
6. As a Reviewer, I want to see my own display name echoed back after entering the
   credential, so that I know which account the server will post as.
7. As a Reviewer, I want my credential kept in the operating system's keychain, so that a
   long-lived secret is not sitting in a file in my home directory.
8. As a Reviewer, I want the server to refuse to run rather than fall back to writing my
   token to disk when the keychain is unavailable, so that a degraded environment cannot
   silently downgrade my security.
9. As a Reviewer, I want the setup page to close itself once I have entered a credential,
   so that a form which accepts secrets is not left listening.
10. As a Reviewer, I want to be warned when my API token expires within a week, so that I
    renew it on a Tuesday morning rather than mid-review.
11. As a Reviewer, I want the setup page to reappear when my credential stops working, so
    that an expired token is a two-minute fix rather than a support question.
12. As a Reviewer, I want the setup page never to appear because a Pull Request asked for
    it, so that no text in a repository can raise a credential form in front of me.

### Reading a Pull Request

13. As a Caller, I want to name a Pull Request with the URL a human pasted, so that I do
    not have to take a URL apart and reassemble it correctly.
14. As a Caller, I want to name a Pull Request with a short workspace/repo/id form, so
    that I can refer to one compactly when a human gave me a number.
15. As a Caller, I want a clear error listing the accepted forms when I name a Pull
    Request wrongly, so that I recover on the next call rather than guessing.
16. As a Caller, I want the Pull Request's state before I review it, so that I frame a
    merged or declined one as follow-up rather than as a gate.
17. As a Caller, I want the Pull Request's title, description, author and branches, so
    that I can judge the change against what it claims to do.
18. As a Caller, I want the Review Basis returned alongside the Pull Request, so that
    everything I post can be tied to the commit I actually read.
19. As a Caller, I want a manifest of changed files before any diff content, so that I can
    decide where to spend attention instead of reading forty files to find three.
20. As a Caller, I want the manifest to flag binary, generated and lockfile entries, so
    that I do not waste a review commenting on a checksum.
21. As a Caller, I want the diff for one file at a time, so that a large Pull Request does
    not exhaust my context before I have said anything.
22. As a Caller, I want the whole diff in one call when the Pull Request is small, so that
    a three-file change does not cost four round-trips.
23. As a Caller, I want to be told plainly when a response was truncated, so that I
    aggregate rather than reason from a partial listing I mistook for a complete one.
24. As a Caller, I want to read a whole file at the Review Basis, so that I can judge a
    hunk whose meaning depends on code above it.
25. As a Caller, I want to list a directory, so that I can find the test file that should
    have changed alongside the code.
26. As a Caller, I want to search code across the repository, so that I can check whether
    the function being changed has other callers.
27. As a Caller, I want the Pull Request's commits, so that I can read the author's intent
    in their own words.
28. As a Caller, I want a file's commit history, so that I can see what the last change to
    it was trying to do before I call this one wrong.
29. As a Caller, I want repository metadata, so that I know the default branch and whether
    I am looking at the mainline.
30. As a Caller, I want everything I fetch marked as untrusted content, so that I treat a
    Pull Request description as data rather than as instructions addressed to me.

### Posting a Review

31. As a Caller, I want to post one Inline Comment against a file and line, so that
    feedback appears next to the code it is about.
32. As a Caller, I want to name a line as added, removed or context rather than as an
    old-side or new-side number, so that I do not orphan the comment by picking the wrong
    one.
33. As a Caller, I want to anchor a comment to a range of lines, so that a Finding about a
    block is not pinned to an arbitrary line within it.
34. As a Caller, I want my Anchor rejected before anything is posted when it is not in the
    diff, so that an invented line number is an error rather than a comment floating in
    the wrong place.
35. As a Caller, I want a rejected Anchor to name the nearest valid lines, so that I can
    correct it on the next call.
36. As a Caller, I want to post many comments in one call, so that a whole Review either
    validates or fails before any of it reaches the Pull Request.
37. As a Caller, I want per-comment results back from a batch, so that I know exactly what
    landed if the network failed halfway through.
38. As a Caller, I want a batch never rolled back after a partial failure, so that comments
    a human may already have read are not deleted underneath them.
39. As a Caller, I want to post one Summary Comment carrying the Review as a whole, so that
    a human gets the shape of it before the line-by-line detail.
40. As a Caller, I want each Finding to carry a Severity and a category, so that a reader
    can tell a blocking defect from a naming quibble.
41. As a Caller, I want Findings rendered consistently, so that every Review from this
    server reads the same way regardless of which model produced it.
42. As a Reviewer, I want every posted comment to say it was machine-generated, so that my
    colleagues are not misled about how much human scrutiny the change received.
43. As a Reviewer, I want my own name in that footer, so that a colleague knows who to ask
    about a comment they disagree with.
44. As a Caller, I want the Review refused when the branch was force-pushed since I read
    the diff, so that I do not attach eight comments to code that no longer exists.
45. As a Caller, I want to read existing comments before I write any, so that I neither
    repeat a point somebody already made nor talk past an open thread.

### Reviewing the same Pull Request again

46. As a Reviewer, I want one Summary Comment kept current rather than a new one each time,
    so that the Pull Request does not accumulate five stale summaries.
47. As a Caller, I want to update only the Summary Comment this server wrote, so that I
    cannot overwrite a human's words.
48. As a Caller, I want the server to verify a comment's author before updating it rather
    than trusting the id I passed, so that a mistaken argument cannot edit somebody else's
    comment.
49. As a Caller, I want to be refused when I post an Inline Comment identical to one
    already at that file and line, so that a re-review does not duplicate itself.
50. As a Caller, I want previous comments of ours whose Anchors have gone stale surfaced
    and marked, so that I can acknowledge them in the new Summary.
51. As a Reviewer, I want stale comments left in place rather than tidied away, so that a
    conversation a colleague replied to is not destroyed to shorten a list.

### Containment

52. As a Reviewer, I want the server to be incapable of merging, approving or declining a
    Pull Request, so that a model reviewing my code cannot land it.
53. As a Reviewer, I want that guarantee to survive a careless refactor, so that it rests
    on more than somebody remembering the rule.
54. As a Reviewer, I want the server to refuse to start with no repository allowlist, so
    that "every repository I can reach" is never the accidental default.
55. As a Reviewer, I want a request against a repository outside the allowlist refused
    before it is sent, so that a confused or injected Caller cannot reach past the
    boundary.
56. As a Reviewer, I want code search results filtered against the allowlist after they
    come back, so that a workspace-wide search cannot leak a repository I did not list.
57. As a Reviewer, I want the documentation to be honest that Bitbucket offers no
    comment-only permission, so that I understand what actually protects me.
58. As a Reviewer, I want the server to tell me at startup if my token grants more than it
    needs, so that over-granting is a visible problem rather than a silent one.
59. As a Reviewer, I want the credential entry page reachable only from my own machine, so
    that nothing on the network can reach a form that accepts secrets.
60. As a Reviewer, I want that page to reject requests originating from a web page, so that
    a site I happen to have open cannot post to it.
61. As a Reviewer, I want the page's setup link to work exactly once, so that a leaked URL
    is not a standing invitation.

### Operating

62. As a Reviewer, I want to check my configuration and credential from the command line
    without starting a review, so that I can diagnose setup problems directly.
63. As a Reviewer, I want logs on stderr and never on stdout, so that the MCP stream is not
    corrupted by a stray line of output.
64. As a Reviewer, I want an error that names the tool or command which resolves it, so
    that a failure teaches me the next step.
65. As a Reviewer, I want the server to work identically on Windows, macOS and Linux, so
    that the team shares one setup story.

## Implementation Decisions

### Shape and layering

The server carries Reviews; it does not form them (ADR-0001). It holds no model
credential, no review prompt, and no rules about what makes code good. Requests to make
the reviews better belong to the Caller, not here.

Layering follows the sibling `streams-postgres-mcp` spine: configuration is plain data
that imports nothing; the Bitbucket client imports configuration but knows nothing of
MCP; the tool module is the only place that imports FastMCP; an entrypoint wires them
together. Arrows point one way. The practical test of the layering is that every safety
property can be exercised as a plain function call with no MCP client running.

Stack matches the sibling: Python 3.13, uv, FastMCP, pydantic-settings, loguru, ruff,
pytest. Transport is stdio, single-user, Bitbucket Cloud only. An architecture document
explains the shape and the reasoning in the sibling's register.

### The tool surface

Eleven tools, `bitbucket_`-prefixed (ADR-0005):

- `bitbucket_get_repository` — repository metadata and default branch
- `bitbucket_get_pull_request` — metadata, state, branches, and the Review Basis
- `bitbucket_get_pull_request_changes` — the diffstat manifest
- `bitbucket_get_pull_request_diff` — the whole diff, or one file via an optional path
- `bitbucket_get_commits` — exactly one of a ref or a Pull Request, erroring on both or
  neither
- `bitbucket_get_file` — file content at a ref
- `bitbucket_get_directory` — directory listing at a ref
- `bitbucket_search_code` — repository-scoped code search
- `bitbucket_get_pr_comments` — existing comments with author and Anchor state
- `bitbucket_add_pr_comment` — one comment or many
- `bitbucket_update_pr_comment` — the Summary Comment only

There is no auth-status tool. Authentication problems surface as actionable errors on
whichever tool the Caller actually wanted, and humans get the same answer from a check
flag. A status tool is one the model calls speculatively and then reasons about.

A Pull Request is addressed by a single string accepting either a Bitbucket URL or the
workspace/repo/id shorthand, parsed in one place — which also gives the Allowlisted
Repository check a single chokepoint to run against.

### Diff handling

Bitbucket's diff endpoint takes no path parameter, so the per-file mode is served by
fetching the whole diff once, caching it against the Review Basis, and slicing locally.
Browsing a forty-file Pull Request file by file therefore costs one network fetch, not
forty. The manifest comes from the diffstat endpoint natively.

Responses are markdown rather than JSON — markedly cheaper in tokens for tabular and
diff content, and every consumer is a language model reading text. Truncation is always
stated explicitly, never silent.

### Anchors and the Review Basis

Callers express an Anchor as a path, a line and a side of added, removed or context; the
server translates to Bitbucket's old-line and new-line fields. This is deliberate: those
raw fields are the single easiest thing on this API to invert, and inverting them
produces a comment attached to the wrong code rather than an error.

Every Anchor is validated against parsed diff hunks before anything is posted, and a
rejection names the nearest valid lines so the Caller recovers on the next call. The
Review Basis — the source commit the diff was read at — is required on every post and
re-checked against the Pull Request's current head; if it moved, the whole batch is
refused with an instruction to re-fetch. Posted Anchors are re-read afterwards and any
that came back orphaned are reported.

### Findings and rendering

A Finding carries a Severity, a category, a message and an Anchor. Severity reuses the
existing house ladder — CRITICAL, HIGH, MEDIUM, LOW, with block, warn, info and note
semantics — rather than introducing a second vocabulary into the same organisation. The
server renders Findings to markdown consistently: it has an opinion about layout and
none about correctness, which is not a violation of ADR-0001 but the boundary of it.

The Summary Comment is one canonical comment per Pull Request, identified by a stable
marker the server embeds in the body, and updated in place on re-review rather than
duplicated. Inline Comments are deduplicated by path, line and body.

Every comment gets an Attribution Footer naming it machine-generated and naming the
Reviewer. It is appended by the renderer, not supplied by the Caller, and no argument
suppresses it.

### Containment

The write ceiling rests on three mechanisms that fail differently (ADR-0002), because
Bitbucket sells no permission separating commenting from merging:

1. No tool exists that can approve, decline or merge.
2. A single chokepoint in the HTTP client permits GET anywhere under an Allowlisted
   Repository, POST only to a Pull Request's comments collection, and PUT only to a
   single comment. DELETE is permitted nowhere; nothing in this server removes a comment.
3. Branch restrictions on the repository, enforced by Bitbucket rather than by this code
   — a documented prerequisite the server cannot impose, only check for.

The allowlist is required, and must be non-empty and non-wildcard or the server refuses
to start. Startup also inspects the credential's granted scopes and refuses to run if it
carries more than reading a repository and writing to Pull Requests, so over-granting is
a visible failure rather than latent power.

Code search needs its own guard (ADR-0006): Bitbucket's search is workspace-scoped and
its repository filter is a substring inside a caller-supplied query string. The tool
therefore requires an explicit repository argument and composes the filter itself, and
the returned results are filtered by repository against the allowlist before any of them
reach the Caller. Composing a string and then trusting it is the mistake worth not
making twice.

All fetched content is wrapped in explicit untrusted-content delimiters carrying a
standing notice that it is data, never instructions.

### Authentication

A per-device Atlassian API token, entered once, kept in the OS keychain alongside the
Reviewer's account email and the token's expiry date (ADR-0003). OAuth is not
implemented: Bitbucket Cloud does not support PKCE, so every device would need either a
hand-registered consumer or a secret shipped in the source, and OAuth's compensating
advantage — tighter scoping — does not exist. App passwords were removed in July 2026.

Basic authentication uses the Atlassian account email, never a Bitbucket username. The
credential is verified against the current-user endpoint at entry and the returned
display name shown back before anything is stored.

The setup page is served by the server itself while it holds no usable credential
(ADR-0004), a deliberate departure from the separate credential-manager process the
sibling repository uses. It binds loopback on a random port, requires a one-time token
carried in the URL, validates origin and host headers against DNS rebinding, sets no
CORS headers, and shuts down on first successful save or after five minutes. It re-opens
only on observed facts — credential missing, past its stored expiry, or a real 401 from
Bitbucket — and never because a tool argument asked for it. The one-time token reaches
the Reviewer through the Caller and is therefore transcript-visible; that is accepted
knowingly and documented rather than pretended otherwise.

### Configuration

Split by how secret each piece is, following the sibling: the repository allowlist and
server settings are committable configuration; the credential lives only in the keychain
and never in a file. Logs go to stderr, never stdout, because a stray line on stdout
corrupts the MCP stream in a way that is miserable to debug. A check flag validates
configuration and credential and exits with a shell status.

## Testing Decisions

A good test here exercises external behaviour — what a Caller can do, what the server
refuses, what actually reaches Bitbucket — and never asserts on internal structure.
Tests are Arrange-Act-Assert with names stating the behaviour: "refuses a batch when the
Review Basis has moved", not "test_post_2". The coverage floor is 80%, but the
containment properties are expected at 100%: a containment rule with no test is a
containment rule that will be refactored away by someone who did not read ADR-0002.

### The seams

**One invented seam: the outbound HTTP transport.** The Bitbucket client accepts an
injected async HTTP client at construction, and tests supply a mock transport.
Everything above the wire stays real in every test — URL parsing, allowlist enforcement,
the write chokepoint, hunk parsing, Anchor validation, Basis comparison, Finding
rendering and the Attribution Footer. This mirrors the prior art in
`streams-postgres-mcp`, where the entire safety surface is testable as plain function
calls with no protocol running.

**One borrowed seam: the keychain backend.** Credential storage uses the keyring
library's own swappable backend, set to an in-memory implementation under test, rather
than a bespoke storage abstraction invented purely for testability.

**Deliberately not seams.** The loopback setup application is exercised as an ASGI app
through an in-process transport, so its routing, its origin and host validation and its
one-time-token handling are all real. Opening a browser is a single monkeypatched call.
Expiry logic takes dates as arguments rather than reading a clock.

### What gets tested

- **Reference parsing**: both accepted Pull Request forms, and rejection of malformed
  ones with an error naming the accepted shapes.
- **The allowlist**: requests inside it pass; requests outside it are refused before any
  request is made; startup fails on an empty or wildcard list.
- **The write chokepoint**: merge, approve, decline and repository-write URLs are refused
  however they were constructed; DELETE is refused everywhere; the two permitted write
  shapes pass. This is the test that must survive every future refactor.
- **Anchor translation and validation**: added, removed and context sides map to the
  correct Bitbucket fields; an out-of-hunk line is rejected before any post with the
  nearest valid lines named; multi-line ranges round-trip.
- **Review Basis**: a batch is refused when the source commit has moved and accepted when
  it has not.
- **Batch semantics**: one invalid Anchor prevents the whole batch from posting; a
  mid-batch network failure yields per-comment results and no rollback.
- **Rendering**: Severity and category render consistently; the Attribution Footer is
  always present; no argument suppresses it.
- **Summary Comment lifecycle**: the first review posts, the second updates in place, and
  an update against a comment authored by somebody else is refused.
- **Deduplication**: an identical Inline Comment at the same path and line is refused.
- **Code search**: the repository filter is composed by the server, and a planted
  out-of-allowlist result in the response is filtered out before it is returned.
- **Credential flow**: successful entry stores and echoes the display name; a wrong email
  fails at entry; an unavailable keychain fails loudly rather than writing a file.
- **The setup listener**: absent when a credential exists, present when it does not,
  rejects a foreign origin or host, rejects a wrong or reused one-time token, and closes
  after a successful save.
- **Scope enforcement**: startup refuses a credential granting more than the server needs.

### Fixtures

Response fixtures are captured from real Bitbucket responses — one Pull Request, its
diff, its diffstat, its commits and its comments — rather than hand-authored JSON.
Hand-written fixtures agree with our misunderstandings; recorded ones do not. Testing at
the transport seam is only as trustworthy as its fixtures, and this is the mitigation.

## Out of Scope

- **Bitbucket Data Center / Server.** Different endpoints, different comment anchoring,
  different auth. Cloud only.
- **OAuth 2.0 in any form** (ADR-0003).
- **Unattended CI review.** No webhook, no HTTP transport, no multi-tenant identity
  mapping. If it arrives later it wants a Repository Access Token and a service shape,
  neither of which is designed here.
- **Approving, declining, merging, or any Pull Request state change.**
- **Deleting comments**, including the server's own.
- **Creating or editing Pull Requests**, branches, or files.
- **Any judgement about code quality.** No review prompt, no rules, no severity
  heuristics, no embedded model (ADR-0001).
- **A local git clone or working tree.** Everything comes from the API.
- **Tasks, approvals, reactions and other Pull Request sub-resources.**
- **Multi-workspace search.** Search is repository-scoped by construction.
- **A persistent credential-administration UI** of the kind the sibling repository ships.
  The setup page is a bootstrap and nothing more.

## Further Notes

**Prior art to read before starting.** `streams-postgres-mcp` next door establishes the
layering, the configuration split, the stderr logging rule, the markdown-response
convention and the error-that-teaches style; its architecture document is the register
to match. The existing `bitbucket-pr-review` skill in the user's global skills directory
has working REST mechanics worth borrowing — but note that its claim about which line
field is which is misleading, and that it predates the app-password removal.

**Facts established during design, worth not re-deriving.** Bitbucket Cloud's inline
comment fields put the new-version line in one and the old-version line in the other,
and the pairing is easy to invert. The diff endpoint has no path parameter. Code search
is workspace-scoped with a substring repository filter. App passwords were removed on 28
July 2026. Bitbucket Cloud does not support PKCE. The permission to comment is the
permission to merge.

**Two decisions to revisit if circumstances change.** ADR-0004's in-process setup page is
defensible only because the listener exists solely while the server has no credential; if
that window ever widens, the argument stops working and the separate process the sibling
uses becomes correct. And the named second consumers — Claude Desktop and the Agents SDK
— have not been exercised; if neither turns out to be real, the existing skill already
covers the Claude Code case and this project is a learning exercise, which changes how
much polish is worth buying but not the design.

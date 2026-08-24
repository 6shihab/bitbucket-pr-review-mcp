# Architecture

This is an MCP server that lets a language model read a Bitbucket Cloud pull request and
leave comments on it. It supplies material and posts words. It does not form opinions,
and it cannot merge anything — with one important caveat about that second claim, set out
under [What actually stops it merging](#what-actually-stops-it-merging), which is the
section to read if you read only one.

The register of this document is deliberate: it explains the shape, and more usefully why
it is that shape. Several of the decisions below look arbitrary until you know the fact
that forced them, and most of those facts were expensive to find.

---

## The shape

Eleven tools, one HTTP client, one guard, and a spine of small modules that each know one
thing. The layering is one-way and it is the property that keeps the safety arguments
short.

```
references.py     names a repository or a pull request      (imports nothing)
credentials.py    an email and a token, and its expiry      (imports nothing)
render.py         markdown, and the untrusted-content fence (imports nothing)
        ↓
settings.py       configuration, and the Allowlist
scopes.py         what a token is allowed to do
keychain.py       where the credential lives
        ↓
guard.py          the one place a request can be refused
client.py         the only thing that talks to Bitbucket
        ↓
pullrequests.py  diffs.py     changes.py   source.py    commits.py
comments.py      repositories.py           search.py
anchors.py       findings.py  posting.py   review.py    summary.py
verify.py        gate.py      setup_app.py setup_listener.py
        ↓
server.py         the only module that knows FastMCP exists
__main__.py       the entrypoint: stdio, logging, --check, --setup
```

Nothing below the line imports anything above it. In particular:

- **Configuration imports nothing.** `settings.py` and `references.py` are plain data, so
  the allowlist can be tested without a network, a protocol, or a credential.
- **The Bitbucket client knows nothing about MCP.** Every domain module takes a
  `BitbucketClient` and returns dataclasses. You can exercise the entire read and write
  surface as ordinary function calls, which is what the tests do.
- **One module imports FastMCP.** If `server.py` were deleted, everything else would still
  work; it would just have no protocol. That is what makes "the safety properties do not
  live in the tool layer" a fact rather than an aspiration.

The layering is not decoration. Mechanism 2 below is a claim about *every* outbound
request, and the only reason it can be checked by reading one function is that there is
only one place requests are made from.

## What actually stops it merging

**Bitbucket sells no permission that separates commenting from merging.**
`write:pullrequest:bitbucket` grants create, update, comment, approve, decline *and*
merge as one indivisible unit. There is no comment-only scope, no token type that has
one, and no repository permission level that has one. Any design claiming "the credential
physically cannot merge" is wrong. **This one does not claim it.** The credential this
server holds is capable of merging your pull request. What stops it is three mechanisms
that fail differently, plus one you configure yourself.

**1. No such tool exists.** Nothing in the tool surface approves, declines or merges.
This is the mechanism that fails first, because it fails the moment somebody adds a tool
without reading this document.

**2. One chokepoint in the HTTP client** — [`guard.py`](../src/bitbucket_pr_review_mcp/guard.py).
Every outbound request passes `assert_permitted` before the transport sees it. The rule
table is tiny and positive: everything not named is refused.

| Method | Permitted |
|---|---|
| GET | `/2.0/user`; anything under an allowlisted repository; code search in a workspace the allowlist reaches |
| POST | `.../pullrequests/{id}/comments`, and nothing else |
| PUT | `.../comments/{id}`, and nothing else |
| DELETE | Nothing, anywhere |

The guard normalises the path first — query and fragment discarded, `..` resolved,
repeated slashes collapsed — because a chokepoint that can be walked around by spelling a
path differently is decoration. It catches what mechanism 1 cannot: a future tool that
forgets the rule, a bug in URL construction, a refactor that widens something by accident.
It does not care how the request was formed.

This is also why redirects are followed *here* rather than by httpx. Bitbucket answers
both `/diff` and `/diffstat` with a 302, and `follow_redirects=True` would send the second
request without passing the guard. Each hop is re-checked, and only GET is followed.

**3. Nothing deletes.** No tool removes a comment and the guard refuses DELETE everywhere.
A stale comment may already carry a colleague's reply, and destroying a conversation to
tidy a list is the worse outcome. This is also why a half-posted batch is never rolled
back.

**4. Branch restrictions on the repository — the one this code cannot impose.** Restrict
who may merge to your default branch, and require approvals. Bitbucket enforces that, so
it survives every mistake made in this repository, including the ones not yet written.
It is a prerequisite, documented in the [README](../README.md), and it is the only
mechanism on this list that does not depend on this code being correct.

Bounding all of it: the **Allowlisted Repository** set. Membership is configured, never
inferred from what the credential happens to reach, and the server refuses to start with
a missing, empty or wildcard list — an absent list is indistinguishable from permission to
touch every repository the credential can see.

See [ADR-0002](adr/0002-comment-only-blast-radius.md).

## Reading: discover, then detail

A Caller that asks for the whole diff first spends most of its context on files it was
never going to comment on. So the read surface is shaped as a funnel:

1. `bitbucket_get_pull_request` — state, branches, and the **Review Basis**, the source
   commit everything later is validated against.
2. `bitbucket_get_pull_request_changes` — one row per changed file with counts, flagging
   binary, generated and lockfile entries. Flags are advisory and nothing is filtered out;
   the point is that a lockfile diff should not attract a review comment about a checksum.
3. `bitbucket_get_pull_request_diff` — the whole diff, or one file.

Bitbucket's diff endpoint takes no `path` parameter, so per-file reading is served by
fetching the whole diff once, caching it **against the Review Basis**, and slicing locally
([`diffs.py`](../src/bitbucket_pr_review_mcp/diffs.py)). Browsing a forty-file pull request
file by file costs one fetch, not forty. The Basis is the cache key and that is the
load-bearing part: a diff cached against the pull request would still be served after a
force-push, which is a diff of code that no longer exists — the worst thing to review
against, because it looks fine.

The rendered diff carries an **anchor gutter**:

```
   12    12 |      session = build_session()
   13       | -    return session.post(UPSTREAM, json=payload)
         13 | +    for attempt in range(RETRIES):
```

The numbers are ours; everything after the `|` is Bitbucket's, and the response says so.
They exist because working out that the fourth `+` in a hunk is line 16 means tracking two
counters that advance at different rates — arithmetic a language model gets wrong, and a
wrong line number is a comment on the wrong code.

ADR-0006 widens this further: `bitbucket_get_file`, `bitbucket_get_directory`,
`bitbucket_get_commits` and `bitbucket_search_code` read any file in an allowlisted
repository, not only the files the pull request touched. A reviewer that cannot look
around is a linter. The cost is honest — a prompt-injected Caller can read repository
contents beyond the change — and the allowlist is what bounds it.

**Code search needs its own guard**, and gets two. Bitbucket has no repository-scoped
code search: the endpoint is workspace-scoped and its repository filter is the substring
`repo:name` *inside a caller-supplied query string*. Enforcing an allowlist by composing
that string would be enforcement by concatenation. So
[`search.py`](../src/bitbucket_pr_review_mcp/search.py) checks the repository itself
before composing anything (the guard can only see the workspace), refuses a query carrying
its own `repo:`/`project:`/`workspace:`/`org:`/`user:` term, and checks every returned
result against the allowlist on the way back. A result whose origin cannot be read is
discarded: this is the backstop, and a backstop that guesses is not one.

## Everything fetched is untrusted

A pull request's title, description, code and comments are written by whoever opened it.
Everything that comes back from Bitbucket is wrapped in an explicit fence carrying a
standing notice that it is data and never an instruction. This server's own words —
truncation notices, ownership flags, guidance — sit *outside* the fence, because a Caller
that cannot tell the two apart is one hostile line away from being steered.

Where a flag could be forged inside the fence (a comment body claiming `[ours]`), the
guidance above the fence states that the flag was computed by this server from account
ids, and the write path re-reads authorship rather than trusting the text.

## Writing: three checks, then one POST at a time

`bitbucket_add_pr_comment` takes one finding or a whole review. Before anything leaves
the process:

1. **Every Finding is well formed** — a severity on the house ladder (CRITICAL/HIGH/
   MEDIUM/LOW → block/warn/info/note), a message, a category.
2. **The Review Basis still matches the pull request head.** The comparison is
   prefix-aware, because the pull request payload abbreviates the hash to twelve
   characters while `/commits` spells forty; `==` would refuse every write.
3. **Every Anchor exists in the diff hunks at that Basis.** An invented line is refused
   with the nearest valid lines *and their text*, so the next call is right rather than
   another guess.

Only then does the first POST go out. That ordering is what makes the guarantee — *a
partial write can only come from the network, never from bad input* — true rather than
aspirational. One bad anchor refuses the whole batch, listing every problem at once.

When the network does fail halfway, nothing is rolled back. The report says exactly what
landed and what to re-post. A 401 stops the rest as `not attempted`, since it will not fix
itself; a 500 lets the batch continue.

**Anchoring** is the part most likely to be got subtly wrong, so it lives in one place
([`anchors.py`](../src/bitbucket_pr_review_mcp/anchors.py)). A Caller names a path, a line
and a side (`added`, `removed`, `context`); the server translates:

| Side | Bitbucket |
|---|---|
| added, context | `inline.to` — the new file's numbering |
| removed | `inline.from` — the old file's numbering |
| a range | `start_to`..`to`, or `start_from`..`from` |

Inverting `from` and `to` does not raise an error. It attaches the comment to a different
line of real code, which is worse than failing, because somebody acts on it.

**Re-review hygiene.** A finding identical to a comment already at that line is skipped
rather than repeated — comparing what was said, not who said it, and normalising away this
server's own severity heading and footer so a repeat is recognised as one. Our own
comments whose anchors have gone stale are surfaced for the summary to acknowledge, and
left exactly where they are.

**Every posted comment carries the Attribution Footer**, naming it machine-generated and
naming the Reviewer whose account it posts under. There is no argument that removes it,
and a message carrying its own fake footer gets the real one appended anyway.

## The Summary Comment, and the only other write

One summary per pull request, updated in place rather than stacked. The server recognises
its own by a marker embedded in the comment body — **the only state this server keeps**,
deliberately stored where it cannot drift from reality. There is no database, and after a
restart the marker is still there.

The marker lives inside the Attribution Footer rather than in an HTML comment. Bitbucket
*escapes* HTML rather than dropping it, so `<!-- ... -->` renders as visible angle
brackets at the top of the comment — precisely the stray-looking text a human tidies away,
which would silently break the one guarantee this feature makes. That was found by posting
one and reading the rendered body back.

The PUT is permitted only against a comment this server authored, **verified by re-reading
the comment from Bitbucket**, never by trusting an id the Caller passed. A comment written
by anyone else is refused by name. A deleted comment is refused too: Bitbucket keeps the
id long after the body is gone.

The tally is counted from the comments actually on the pull request, not from what the
Caller says it posted — a summary that disagrees with the page it heads is worse than no
summary. The Caller writes the verdict; this server counts and lays out. That split is
[ADR-0001](adr/0001-thin-tool-server-no-embedded-model.md).

## The credential

An Atlassian API token plus the **Atlassian account email** — not a Bitbucket username,
and not the token's name. The other two return 401 with an empty body, which is among the
least diagnosable failures on this API, so the distinction is enforced at construction
rather than discovered during a review.

It lives in the **OS keychain and nowhere else**. There is no file fallback and no
development mode: a fallback is how a secret ends up in a synced dotfile, and the failure
would be silent. An unreachable keychain stops the server.

**Setup runs inside this process** ([ADR-0004](adr/0004-setup-listener-lives-inside-the-server.md)).
While there is no usable credential, the server opens a loopback listener on an
OS-assigned port and hands the Reviewer a URL. The page names the scopes, takes the email
and token, **verifies them against Bitbucket and shows the account's display name back
before storing anything** — no status code catches a token pasted from the wrong account,
but seeing the wrong person's name does. It is bound by a one-time token in the URL,
`Origin` and `Host` validation, no CORS headers, and a five-minute life; it closes on the
first successful save.

The listener opens on **observed facts only**: no credential, a stored one past its
expiry, or a real 401. Never because a tool argument asked for it. A pull request
description that could raise a credential form would be phishing aimed at a Reviewer who
is already expecting the tool to do things.

Four scopes are required, and a token granting more is refused at the form and again at
startup:

```
read:user:bitbucket          read:repository:bitbucket
read:pullrequest:bitbucket   write:pullrequest:bitbucket
```

## Running it in a container

The server is one process reading stdin and writing stdout, so the image has no port, no
daemon and no entrypoint script — `docker run -i` and talk to it. What the container
cannot do is the interesting part, and both are consequences of decisions made earlier
rather than oversights:

- **It has no keychain**, so it cannot store a credential. It is given one through the
  environment instead, which is worse and is treated as worse: nothing falls back to it,
  both variables must be set, the store refuses to save, and startup says at WARNING what
  was traded. See [ADR-0007](adr/0007-the-container-is-given-its-credential.md).
- **It cannot open the setup page.** The listener binds a random loopback port *inside*
  the container, which a browser on the host cannot reach — and there would be nowhere
  durable to save what was entered. `--setup` exits 2 naming where setup can be run.

The allowlist is mounted read-only rather than baked in: it names the repositories the
server may touch, and that list belongs to whoever runs the image. The image itself
carries only code — no writable state, no root, no capabilities.

## Tests

The seam is the transport and nothing else. Every test builds a real `BitbucketClient`
over a mock transport, so the guard, the allowlist, path normalisation and response
handling are all production code; only the wire is fake. The setup page is tested as an
ASGI app in process, with real routing and real header validation.

`tests/recorded/` holds responses captured from a live pull request, exercised by
`test_recorded_responses.py`. They exist because hand-written fixtures agree with our
misunderstandings and recorded ones do not — and they earned their keep immediately, as
the next section records.

## Facts worth not re-deriving

Each of these cost something to establish. Several were wrong in the first implementation
and were corrected only by running against the real API.

- **The diff endpoint takes no `path` parameter** — only `context`. Per-file reading is
  local slicing of one cached fetch.
- **`/diff` *and* `/diffstat` answer with a 302.** A client that does not follow redirects
  cannot read a real pull request at all. Follow them through the guard.
- **`source.commit.hash` on a pull request is abbreviated to twelve characters**, while
  the same commit is spelled in full inside `links` and by `/commits`. Compare
  prefix-aware or every write is refused.
- **A code search result carries no repository field** — only `file.links.self.href`,
  whose path names the repository. The first allowlist backstop read a field that does not
  exist and discarded every legitimate match.
- **Granular scopes do not nest.** `write:pullrequest:bitbucket` does *not* grant reading
  a pull request. The older app-password scope `pullrequest:write` did imply read, which
  is the kind of asymmetry that survives in nobody's memory.
- **`GET /2.0/user` needs `read:user:bitbucket`.** Without it there is no way to know
  whose comments are ours, and re-review stacks duplicates.
- **A 403 body names the scope it wanted**, in `error.detail.required`. Repeat it rather
  than guessing.
- **`inline` has `start_from` and `start_to`**, which the API reference does not mention.
  They are how a comment anchors to a block: `start_to` is the first line, `to` the last.
- **Deletion is a tombstone.** A deleted comment keeps its id and loses its body, so
  "update comment 5001" can be a write into a grave.
- **Bitbucket escapes HTML in comment bodies** rather than dropping it.
- **Bitbucket Cloud does not support PKCE** — its token endpoint demands a `client_secret`
  even when sent a `code_challenge` — and **app passwords were removed on 28 July 2026**.
  Together those are why authentication is a pasted API token rather than OAuth
  ([ADR-0003](adr/0003-atlassian-api-token-not-oauth.md)).

## Where each decision shows up

| ADR | In the code |
|---|---|
| [0001](adr/0001-thin-tool-server-no-embedded-model.md) — carries reviews, does not form them | No prompt anywhere; `findings.py` and `summary.py` lay out what the Caller wrote and count what is posted |
| [0002](adr/0002-comment-only-blast-radius.md) — comment-only blast radius | `guard.py`'s rule table; the tool list in `server.py`; no DELETE anywhere; `summary.py`'s authorship re-read |
| [0003](adr/0003-atlassian-api-token-not-oauth.md) — API token, not OAuth | `credentials.py`, `keychain.py`, `scopes.py`, and the email trap enforced at construction |
| [0004](adr/0004-setup-listener-lives-inside-the-server.md) — setup inside the server | `setup_app.py`, `setup_listener.py`, and `gate.py`, which decides when it may open |
| [0005](adr/0005-tool-surface.md) — eleven tools | `server.py`; one string names a pull request, parsed in `references.py`; the batch in `review.py` |
| [0006](adr/0006-the-read-surface-is-wider-than-the-pull-request.md) — wider read surface | `source.py`, `commits.py`, `repositories.py`, and `search.py`'s two extra guards |
| [0007](adr/0007-the-container-is-given-its-credential.md) — a container is given its credential | `environment.py`, and the `Dockerfile` that has nowhere to store one |

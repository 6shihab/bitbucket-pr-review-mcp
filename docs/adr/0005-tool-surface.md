# Eleven `bitbucket_`-prefixed tools: read the repository, comment on the pull request

```
bitbucket_get_repository(repo)
bitbucket_get_pull_request(pr)                      -> metadata + Review Basis
bitbucket_get_pull_request_changes(pr)              -> diffstat manifest
bitbucket_get_pull_request_diff(pr, path=None)      -> whole diff, or one file
bitbucket_get_commits(repo, ref=None, pr=None)      -> exactly one of ref/pr
bitbucket_get_file(repo, path, ref)
bitbucket_get_directory(repo, path, ref)
bitbucket_search_code(repo, query)
bitbucket_get_pr_comments(pr)
bitbucket_add_pr_comment(pr, basis, comments=[...]) -> one or many
bitbucket_update_pr_comment(pr, comment_id, body)
```

Five decisions inside it are worth the explanation.

**A Pull Request is named by one string, not a triple.** It accepts a full
`bitbucket.org` URL or `workspace/repo/id` shorthand and parses it server-side. A
triple just relocates URL-parsing into the model, where it fails silently and
inventively. Parsing in one place also gives the Allowlisted Repository check a single
chokepoint to run against.

**Manifest and per-file diff both exist, and cost one network call between them.**
Bitbucket's `/diff` has no `path` parameter — only `context` — so `path` is served by
fetching the whole diff once, caching it against the Review Basis, and slicing
locally. Browsing a forty-file pull request file by file therefore costs one fetch,
not forty, and a small pull request can still take the whole diff in one call.

**`add_pr_comment` takes one comment or many.** A single comment is the natural
one-item call; a multi-item call recovers the property that matters — every Anchor
validated against the diff hunks and the Review Basis re-checked *before anything is
posted*, so a partial write can only come from the network, never from bad input.
Batched calls return a per-comment result and **do not roll back**: deleting comments
a human may already have read is worse than reporting exactly what landed. A second
batch-only tool would have been a coin-flip the model makes on every call, which is
also why `get_commits` is one tool with a required either/or rather than two.

**`update_pr_comment` exists for exactly one job** — keeping one canonical Summary
Comment current across repeated reviews instead of stacking a new one each time. It
is permitted only against a comment this server authored, verified by re-reading the
author. There is no delete (ADR-0002).

**There is no `bitbucket_auth_status` tool.** The same information arrives as an
actionable error on whichever tool the model actually wanted, and humans get it from
`--check`. A status tool is one the model calls speculatively and then reasons about.

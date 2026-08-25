# The read surface covers the whole repository, not just the diff

`get_file`, `get_directory`, `get_commits` and `search_code` let the Caller read any
file in any Allowlisted Repository — not only the files a pull request touched.

This is a deliberate widening. A diff read in isolation produces the worst kind of
review: confident about syntax, blind to whether the changed function has three other
callers, whether the pattern being "fixed" is load-bearing elsewhere, or what the
previous commit to that file was trying to do. A reviewer that cannot look around is
a linter.

The cost is honest: a prompt-injected Caller — and pull request content is written by
whoever opened it — can read repository contents beyond the change under review. The
Allowlisted Repository set is what bounds that, which is why the server refuses to
start with an empty allowlist. Where that set is a whole workspace (`workspace/*`), the
bound is the workspace — worth knowing, because this is the read surface it applies to.

## Code search needs its own guard

`GET /2.0/workspaces/{workspace}/search/code` searches an entire workspace, and its
repository filter is the substring `repo:name` **inside a caller-supplied query
string**. Enforcing an allowlist by composing that string would be enforcement by
concatenation, defeated by a query that adds, removes or duplicates a `repo:` term.

So the tool requires an explicit `repo` argument and builds the filter itself, *and*
the results are filtered by repository against the allowlist before any of them reach
the model. Composing a string and then trusting it is the mistake worth not making
twice.

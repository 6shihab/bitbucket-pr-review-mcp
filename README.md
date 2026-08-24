# bitbucket-pr-review-mcp

An MCP server that lets a language model read a Bitbucket Cloud pull request and leave
comments on it — anchored to the lines they are about, plus one summary at the top.

The server supplies material and posts words. It does not form opinions: the calling model
does the reviewing, and nothing here holds a review prompt or a model credential.

- **How it works and why:** [docs/architecture.md](docs/architecture.md)
- **The decisions behind it:** [docs/adr/](docs/adr/)
- **The language it uses:** [CONTEXT.md](CONTEXT.md)

## What it cannot do

It can create and update comments. It cannot approve, decline or merge a pull request,
cannot write to branches or files, and never deletes a comment.

**Bitbucket does not sell a permission that separates commenting from merging.** The
credential you give this server is capable of merging your pull requests; nothing about
tokens or scopes prevents it. What prevents it is that no tool asks for it, that a single
chokepoint in the HTTP client refuses it however the request is constructed, and — the
part that does not depend on this code being right — **branch restrictions you configure
on the repository yourself**. See
[ADR-0002](docs/adr/0002-comment-only-blast-radius.md), and read
[Before you start](#before-you-start).

## Before you start

**Configure branch restrictions on any repository you allowlist.** In Bitbucket:
*Repository settings → Branch restrictions*. Restrict who may merge to your default
branch, and require the approvals your team expects. Bitbucket enforces that regardless of
what this server does, which makes it the only guarantee here that survives a bug in this
repository. It takes a minute and it is the difference between "we believe this code is
careful" and "it does not matter if it isn't".

## Install

Requires [uv](https://docs.astral.sh/uv/) and Python 3.13. The same three commands work on
Windows, macOS and Linux:

```
git clone <this repository>
cd bitbucket-pr-review-mcp
uv sync
```

Then tell it which repositories it may touch:

```
cp config/repositories.yaml.example config/repositories.yaml
```

...and edit it. The file lists `workspace/repo` entries, holds no secrets, and is meant to
be committed:

```yaml
repositories:
  - streamstech/db-explorer
  - streamstech/lent-manager
```

The server refuses to start without it. An absent list is indistinguishable from
permission to touch every repository your credential can reach, and wildcards are refused
for the same reason.

## Connecting your Bitbucket account

Run setup and open the link it prints:

```
uv run bb-pr-mcp --setup
```

It serves a page on your own machine — loopback only, on a random port, single-use link,
gone after five minutes — asking for your **Atlassian account email** (not your Bitbucket
username, and not the name you gave the token) and an API token. It verifies the pair
against Bitbucket and shows your display name back before storing anything, then puts the
credential in your OS keychain and closes itself.

Create the token at
<https://id.atlassian.com/manage-profile/security/api-tokens> with **exactly** these four
scopes:

| Scope | Why |
|---|---|
| `read:user:bitbucket` | So the server knows whose comments are its own — without it, every re-review stacks duplicates |
| `read:repository:bitbucket` | Reading files and commits around the change |
| `read:pullrequest:bitbucket` | Reading the pull request itself — granular scopes do not nest, so the write scope below does not cover this |
| `write:pullrequest:bitbucket` | Posting and updating comments |

Nothing wider. A token that can also write to a repository, administer one or run
pipelines is refused at the form and refused again at startup.

Enter the token's expiry date when you set it up and you will be warned a week before it
lapses instead of hitting a 401 mid-review.

You never have to run `--setup` explicitly: with no credential stored, every tool answers
with the setup URL instead of an error.

Check it whenever you like:

```
uv run bb-pr-mcp --check
```

That validates the allowlist, the credential and its scopes, prints who you are posting
as, and exits with a shell status — `0` fine, `1` no usable credential, `2` a token whose
scopes are wrong.

### If the keychain is unavailable

The credential goes in the OS keychain and nowhere else — never a file. On macOS and
Windows that works out of the box. On Linux you need a Secret Service (gnome-keyring or
KWallet) running and unlocked; if there is none, the server stops rather than falling back
to a file ([ADR-0003](docs/adr/0003-atlassian-api-token-not-oauth.md)).

## Running it

The server speaks MCP over stdio. Point your client at it:

```json
{
  "mcpServers": {
    "bitbucket-pr-review": {
      "command": "uv",
      "args": ["run", "--directory", "/path/to/bitbucket-pr-review-mcp", "bb-pr-mcp"]
    }
  }
}
```

On Windows use the same shape with a Windows path
(`"C:\\path\\to\\bitbucket-pr-review-mcp"`). Nothing else differs between platforms.

For Claude Code:

```
claude mcp add bitbucket-pr-review -- uv run --directory /path/to/bitbucket-pr-review-mcp bb-pr-mcp
```

## The tools

| Tool | What it does |
|---|---|
| `bitbucket_get_pull_request` | Title, state, author, branches, and the Review Basis |
| `bitbucket_get_pull_request_changes` | Every changed file with counts, flagging binary, generated and lockfile entries |
| `bitbucket_get_pull_request_diff` | The whole diff, or one file — with an anchor gutter showing each line's number |
| `bitbucket_get_pr_comments` | The existing conversation, with anchors, stale flags, and which comments are the server's own |
| `bitbucket_add_pr_comment` | Post one finding or a whole review; validated entirely before any of it is sent |
| `bitbucket_update_pr_comment` | Post or update the one summary comment |
| `bitbucket_get_repository` | Metadata and the default branch |
| `bitbucket_get_file` | Any file in an allowlisted repository, at a ref |
| `bitbucket_get_directory` | A directory listing, at a ref |
| `bitbucket_get_commits` | History for a ref or a pull request |
| `bitbucket_search_code` | Code search inside one allowlisted repository |

A pull request is named by one string: either a Bitbucket URL or the shorthand
`workspace/repo/id`.

## Configuration

Everything below has a working default. Set them in the environment or a `.env` file, all
prefixed `BB_MCP_`:

| Setting | Default | What it does |
|---|---|---|
| `BB_MCP_REPOSITORIES_FILE` | `config/repositories.yaml` | Where the allowlist lives |
| `BB_MCP_LOG_LEVEL` | `INFO` | Logs go to stderr, never stdout |
| `BB_MCP_REQUEST_TIMEOUT_SECONDS` | `30` | Per-request timeout |
| `BB_MCP_MAX_DIFF_CHARACTERS` | `60000` | Diff response ceiling |
| `BB_MCP_MAX_FILE_CHARACTERS` | `40000` | File response ceiling |
| `BB_MCP_MAX_CHANGED_FILES` | `300` | Manifest rows |
| `BB_MCP_MAX_DIRECTORY_ENTRIES` | `200` | Directory rows |
| `BB_MCP_MAX_COMMITS` | `50` | Commit rows |
| `BB_MCP_MAX_SEARCH_RESULTS` | `25` | Search matches |
| `BB_MCP_MAX_COMMENTS` | `200` | Comments read per pull request |

Every ceiling is stated in the response when it bites. Truncation is never silent: a
Caller that cannot tell a truncated diff from a complete one will review the missing half
by assuming it was fine.

## Development

```
uv run pytest                    # the suite
uv run pytest --cov=src          # with coverage
uv run ruff check src tests      # lint
uv run ruff format src tests     # format
```

Tests never touch the network. The seam is the HTTP transport and nothing else, so the
guard, the allowlist and every response reader are exercised as production code.
`tests/recorded/` holds responses captured from a real pull request — see
[docs/architecture.md](docs/architecture.md#tests) for why that matters more than it
sounds.

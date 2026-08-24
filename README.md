# bitbucket-pr-review-mcp

An MCP server that lets a language model read a Bitbucket Cloud pull request and leave
comments on it — anchored to the lines they are about, plus one summary at the top.

The server supplies material and posts words. It does not form opinions: the calling
model does the reviewing, and nothing here holds a review prompt or a model credential.

## Connecting your Bitbucket account

Start the server and it tells you what to do. With no credential stored, every tool
answers with a URL instead of an error; open it and you get a page on your own machine
asking for your **Atlassian account email** — not your Bitbucket username — and an API
token. It verifies the pair against Bitbucket and shows your display name back before
storing anything, then puts the credential in your OS keychain and closes itself.

To do it deliberately rather than on first use:

```
uv run bb-pr-mcp --setup     # opens the page, waits, exits when connected
uv run bb-pr-mcp --check     # confirms configuration, credential and scopes
```

Create the token at <https://id.atlassian.com/manage-profile/security/api-tokens> with
exactly these scopes:

- `read:user:bitbucket` — so the server can show you whose account it connected, and
  later recognise its own comments rather than stacking duplicates
- `read:repository:bitbucket`
- `read:pullrequest:bitbucket` — granular scopes do not nest, so the write scope below
  does not let the server read a pull request
- `write:pullrequest:bitbucket`

Nothing wider: a token that can also write to a repository, administer one, or run
pipelines is refused at the form and refused again at startup.

The credential lives in the OS keychain and never in a file. On Linux that means a
Secret Service — gnome-keyring or KWallet — must be running and unlocked; the server
stops rather than falling back to a file. Enter the token's expiry date when you set it
up and you get a warning a week before it lapses, instead of a 401 mid-review.

Full documentation arrives with ticket 09. Until then:

- The design lives in [CONTEXT.md](CONTEXT.md) and [docs/adr/](docs/adr/).
- The spec and tickets live in [.scratch/bitbucket-pr-review-mcp/](.scratch/bitbucket-pr-review-mcp/).

## What it cannot do

It can create and update comments. It cannot approve, decline or merge a pull request,
cannot write to branches or files, and never deletes a comment.

Bitbucket does not sell a permission that separates commenting from merging, so that
ceiling is not enforced by the credential — it rests on the tools not existing, on a
single chokepoint in the HTTP client, and on branch restrictions you configure on the
repository itself. See [ADR-0002](docs/adr/0002-comment-only-blast-radius.md).

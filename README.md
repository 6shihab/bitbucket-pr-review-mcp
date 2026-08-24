# bitbucket-pr-review-mcp

An MCP server that lets a language model read a Bitbucket Cloud pull request and leave
comments on it — anchored to the lines they are about, plus one summary at the top.

The server supplies material and posts words. It does not form opinions: the calling
model does the reviewing, and nothing here holds a review prompt or a model credential.

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

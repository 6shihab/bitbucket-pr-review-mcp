# The server carries reviews, it does not form them

The server exposes Bitbucket mechanics as tools — fetch the pull request, fetch
the diff, list comments, post comments — and the calling model does the reviewing.
We rejected embedding a model in the server: it would need its own API credential
and its own prompt to version, its cost would be invisible to whoever called it,
and a process that both reads private repository content and makes outbound calls
to a model API is a one-hop exfiltration path in a server whose whole claim is
being secure.

## Consequences

The server forms no opinion at call time, and requests to "improve the review quality"
are answered by editing text under test rather than by tuning a model here.

**Amended by [ADR-0009](0009-the-server-ships-the-review-prompt.md).** This originally
read "the server holds no review prompt and no notion of what makes code good". It now
ships one prompt — text, versioned in this repository, calling nothing. Read ADR-0009
for why that is not the thing this ADR refused; everything above still holds.

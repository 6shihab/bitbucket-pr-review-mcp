# The credential setup page is served by the MCP server itself, while unauthenticated

When no usable credential is in the keychain, the server opens a loopback HTTP
listener and hands the Reviewer a URL where they enter their Atlassian account email
and API token. It shuts the listener down permanently the moment a credential is
saved, and re-opens it only on observed facts — credential missing, past its stored
expiry, or a real 401 from Bitbucket. Never because a tool argument asked it to.

**This deliberately departs from the pattern next door.** `streams-postgres-mcp` keeps
its credential manager in a separate process on the stated grounds that merging them
"would give the widely-reachable process the ability to rewrite its own access
control". A future reader who knows that repository will expect a separate app here
and should know why there isn't one: this server is per-device and single-user rather
than LAN-reachable, and a second installable process to paste one token into is
friction the tool would not survive. The bootstrap-only window is what makes the
trade acceptable — the process can rewrite its own credentials precisely while it
holds none worth protecting.

The listener binds `127.0.0.1` on a random port, requires a one-time token carried in
the URL, validates `Origin` and `Host` to defeat DNS rebinding, sets no CORS headers,
and exits on first successful save or after five minutes.

The re-open trigger is the security-critical part. A listener that could be summoned
by a tool call would let text inside a pull request raise a credential form at the
moment the Reviewer is most primed to fill one in.

## Consequences

The one-time setup token travels to the Reviewer through the calling model, so it is
visible in the transcript. Accepted knowingly: it is loopback-only, single-use, and
expires in minutes.

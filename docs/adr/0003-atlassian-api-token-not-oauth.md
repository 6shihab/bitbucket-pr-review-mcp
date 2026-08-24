# Authentication is a pasted Atlassian API token, not OAuth

Each device holds its own credential: an Atlassian API token, entered once by the
Reviewer along with their Atlassian account email, and kept in the OS keychain.

We rejected the OAuth 2.0 browser flow, which was the obvious choice for the
"click and you're logged in" experience we wanted. Three facts sank it. Bitbucket
Cloud does not support PKCE — its token endpoint demands a `client_secret` even when
sent a `code_challenge` — so every device would need either a per-user OAuth consumer
registered by hand or a shared secret shipped in the source. OAuth's compensating
advantage would have been tighter scoping, and that advantage does not exist: no
Bitbucket scope separates commenting from merging (see ADR-0002). And app passwords
were removed on 28 July 2026, making API tokens the only Basic-auth credential
Bitbucket still accepts.

Two auth paths would have been twice the code and twice the failure surface for no
security gain.

## Consequences

The credential is a long-lived bearer secret rather than a 2-hour access token, so
storage matters more: OS keychain only, never a file, and the server refuses to
start rather than silently downgrading. It carries a user-chosen expiry, so the
server warns when that date is within a week instead of surfacing an unexplained 401
mid-review.

**The shared deployment does not keep this part.** A keychain holds one credential
for the person running the process, which cannot hold a colleague's token on a server
they have never logged into, so the shared server stores credentials in an encrypted
file instead — a downgrade, taken deliberately and documented in
[ADR-0008](0008-the-shared-server-holds-other-peoples-credentials.md) and
[the threat model](../threat-model-shared-store.md). Everything else in this ADR still
holds in both deployments: the credential is an API token, the username is the
Atlassian email, and it is verified at entry.

The Basic-auth username is the **Atlassian account email**. A Bitbucket username or
the token's name returns 401 with nothing useful in the body, so credentials are
verified against `GET /2.0/user` at entry and the returned display name shown back
before anything is saved.

Unattended CI, if it ever arrives, wants a Repository Access Token — not this, and
not OAuth.

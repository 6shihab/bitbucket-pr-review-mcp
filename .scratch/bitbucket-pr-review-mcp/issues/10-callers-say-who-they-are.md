# 10 — Callers say who they are: this server becomes an OAuth authorization server

**Decided 2026-08-25.** The target client is **Claude on the web, on a Team plan**. That
answers the question this ticket was blocked on, and it answers it against the spec's
first guess. Bearer tokens are out. This is an OAuth 2.1 implementation.

## Why the cheap option is not available

Claude does support a fixed credential in a request header (`static_headers`, in beta).
It is the wrong shape for what was asked: an **organization administrator enters one
credential when adding the connector, and Claude sends it on every request**. One
credential, shared by the whole organization. The requirement is the opposite — each
person supplies their own Atlassian email and API token, and posts under their own name.

Everything else Claude supports for remote MCP is OAuth. So the server becomes an OAuth
authorization server, and there is no smaller version of this ticket.

## What that buys, which is more than it costs

The OAuth authorization page **is** the Bitbucket credential page. When somebody connects
the connector, Claude opens our `/authorize` in *their own browser*; that page asks for
their Atlassian email and API token, verifies the pair against Bitbucket, stores it in the
vault, and redirects back with an authorization code.

No setup URL travels through the model's transcript. Ticket 13 existed to defend a public
link against whoever read it first, and that attack — supplying a credential into somebody
else's session — cannot happen when the browser arrived by OAuth redirect carrying its own
`state` and PKCE verifier. ADR-0004's accepted risk is resolved by construction rather than
by a typed code, and ticket 13 shrinks to the form's own protections.

## The shape, from Anthropic's connector documentation

Confirmed 2026-08-25 at `claude.com/docs/connectors/building/authentication`:

- Every MCP request without a valid token answers **401** with
  `WWW-Authenticate: Bearer resource_metadata="..."`. Claude does **not** honour that
  header on a 200.
- `/.well-known/oauth-protected-resource` (RFC 9728). Its `resource` must equal the MCP
  URL **exactly as the Owner types it into Claude**, path included.
- `/.well-known/oauth-authorization-server` (RFC 8414), advertising
  `code_challenge_methods_supported: ["S256"]` and `offline_access` in `scopes_supported`
  — the second is how Claude knows to ask for a refresh token.
- `/authorize` and `/token`. **`/token` must accept
  `application/x-www-form-urlencoded`**; a JSON-only body parser answers 415 and the
  connection fails. `/register`, if implemented, takes JSON instead — different parser,
  same server.
- Redirect URI to accept: `https://claude.ai/api/mcp/auth_callback`, for web, Desktop and
  mobile alike. Claude Code uses an RFC 8252 loopback redirect on a port that changes
  every session, so `localhost`/`127.0.0.1` must match ignoring the port if we want it to
  work there too.
- PKCE **S256 on every request**, no exceptions.
- The `resource` parameter (RFC 8707) is sent on both authorization and token requests,
  and the access token's audience must be validated against it.
- `iss` in authorization responses (RFC 9207), advertised as
  `authorization_response_iss_parameter_supported`.
- Refresh tokens **rotated** — Claude registers as a public client — with the new one
  returned in the same response that invalidates the old. A dead refresh token answers
  `invalid_grant`, not a custom code.
- **Ten seconds** for discovery, registration and token; thirty for refresh. Nothing on
  the token path may call Bitbucket.

## Client registration: pre-registered, not dynamic

Claude supports Dynamic Client Registration and would use it by default, and the same
documentation recommends against it for servers that expect volume — it registers a fresh
client on every new connection. For one team's own connector the Owner can paste a client
id and secret into Advanced settings once, which Anthropic describes as the right choice
when you want a stable OAuth client scoped to one organization.

Pre-registered is therefore the primary path, and it also means **no open registration
endpoint on a host holding the team's credentials**. DCR can be added later if the
friction is real.

## The question this ticket has to answer, and it is not a small one

`/authorize` is reachable by anyone on the internet who has the URL. Claude does not tell
us which Claude user is at the other end — the redirect arrives from the person's browser,
not from Anthropic. So the server cannot assume a caller is on the team, and **enrolment
needs a gate of its own**.

The exposure is smaller than it first looks: an outsider can only enrol *their own*
Atlassian token, bounded by our allowlist, so they would get a server that returns 403s.
What they can do is fill the vault with rows. The proposed gate is an email domain
allowlist checked after the credential verifies against Bitbucket — the account Bitbucket
returns must be `@streamstech.com`, or nothing is stored.

Worth knowing separately: **Anthropic's requests come from `160.79.104.0/21`**. The MCP
endpoint itself can be firewalled to that range, so the part of this server that holds
credentials need not be open to the internet at all. Only `/authorize` has to be, because
a browser goes there.

**Blocked by:** nothing. **Needs, to deploy but not to build:** a public HTTPS hostname
with a real certificate. The issuer URL is configuration.

**Status:** ready-for-agent

- [ ] `/.well-known/oauth-protected-resource` and `/.well-known/oauth-authorization-server`
      serve documents that match the deployed URL exactly, and a test asserts they do
- [ ] Every unauthenticated MCP request answers 401 with a `resource_metadata` pointer
- [ ] Authorization code flow with PKCE S256, refused without a verifier
- [ ] `/token` parses form-urlencoded, answers inside a second, and never calls Bitbucket
- [ ] Access tokens carry an audience, and one minted for another resource is refused
- [ ] Refresh tokens rotate, and a replayed one is refused with `invalid_grant`
- [ ] The `/authorize` page asks for the Atlassian email and API token, verifies them
      against Bitbucket, shows the display name back, and refuses over-broad scopes —
      the existing setup page, moved
- [ ] A verified account outside the allowed email domain is refused and nothing is stored
- [ ] The credential lands in the vault against a person id minted at authorization
- [ ] The person id, never the token, appears in log lines
- [ ] `https://claude.ai/api/mcp/auth_callback` is accepted; an unregistered redirect is not
- [ ] The stdio server keeps working with no authentication, because there is one caller

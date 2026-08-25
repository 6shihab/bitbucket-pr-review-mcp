# 10 — Callers say who they are

**Decided 2026-08-25.** The target client is **Claude on the web, on a Team plan**, added
as a custom connector. That rules out the cheap option and makes this OAuth.

Claude does support a fixed header credential (`static_headers`, beta), and it is the
wrong shape: an **organization administrator enters one credential when adding the
connector, and Claude sends it on every request** — one credential, shared by everybody.
The requirement is the opposite. Every other remote-MCP option Claude offers is OAuth.

The ticket has two halves. **10A is needed whichever way 10B is decided**, so it is built
first and does not wait on anything.

---

## 10A — This server is an OAuth resource server

**Status: the verification is built; the transport wiring waits for ticket 12.**
`tokens.py` turns an `Authorization` header into a `Caller` and a person id;
`discovery.py` produces the metadata document and the `WWW-Authenticate` challenge.
Both are pure and tested against real RSA signatures over a mocked issuer. What is
left is attaching them to requests, which needs an HTTP surface to attach to.

Validate a token, learn who the caller is, find their credential. That code is identical
whether the tokens come from Keycloak or from an authorization server we write, which is
why it is separated out.

- [ ] Every MCP request without a valid token answers **401** with
      `WWW-Authenticate: Bearer resource_metadata="..."`. Claude does **not** honour that
      header on a 200, so a tool-level error is not a substitute
- [x] `/.well-known/oauth-protected-resource` (RFC 9728) serves a document whose
      `resource` equals the MCP URL **exactly as the Owner types it into Claude**, path
      included, and whose `authorization_servers` names the issuer — first entry wins,
      Claude does not fall back to later ones
- [x] Access tokens are verified against the issuer's JWKS: signature, issuer, expiry
- [x] **The audience is checked.** A token minted for another resource is refused, and
      there is a test that mints one and watches it bounce
- [x] The token's subject maps to a person id — `sha256(issuer + subject)`, so the
      vault's one plaintext column is not also a directory of user ids
- [ ] That person's vault row is what the session uses — needs ticket 12's transport
- [ ] A caller whose subject has no stored Bitbucket credential is sent to connect one,
      rather than getting an error that reads like a bug
- [x] Log lines name the person, never the token
- [ ] The stdio server keeps working with no authentication, because there is one caller


**Two checks in `tokens.py` are load-bearing, and a verifier without them looks
identical from the outside:**

* **The algorithm allowlist.** Trusting the token's own `alg` accepts `none`, and accepts
  `HS256` signed with the issuer's *public* key — which anybody can fetch. Both forgeries
  are in the tests, the second hand-crafted because PyJWT refuses to produce it.
* **The audience.** Without it, a token some other service minted for itself is accepted
  here, and its bearer is handed somebody's Bitbucket credential.

Also asserted: RFC 8414's mix-up defence (metadata naming a different issuer is refused),
key rotation causing exactly one refetch, and rubbish key ids being unable to make this
server hammer Keycloak.

## 10B — Where the authorization server comes from

**Decided 2026-08-25: Keycloak, in Docker, alongside the MCP server.**

Either Keycloak issues the tokens and this server only validates them, or the server
implements the authorization endpoints itself. The consequences reach further than the
endpoint list — they decide what "who is this person" means, and whether ticket 13 exists.

### Keycloak, and what it needs

**Standing up 2026-08-25: `docker-compose.shared.yaml` plus
`deploy/keycloak/realm-streamstech.json`.** Keycloak runs, the realm imports, and a
real token from it is accepted by `tokens.py` while four kinds of bad token are
refused. Two defects were found by doing that and would not have been found by
reading — both are now in `docs/architecture.md` and pinned by
`tests/test_realm_configuration.py`:

1. Declaring any `clientScopes` in a realm import **replaces** the built-in set. The
   realm came up without `basic`, so tokens carried no `sub` and every one was
   rejected.
2. A **default** client scope is granted whether or not it is requested. With
   `bitbucket:review` as a default, a token minted for `scope=openid` still carried
   it and the scope check here was decorative. Optional fixes it — and an unasked
   token then has no audience either, so it fails twice.

Keycloak publishes an official MCP authorization-server integration and, since 26.7,
Client ID Metadata Document support. It brings PKCE, refresh rotation, discovery, DCR and
real user management, none of which then has to be written here or reviewed here.

Two workarounds are needed, and both are known rather than discovered later:

1. **Keycloak does not implement RFC 8707.** It ignores the `resource` parameter that
   Claude sends and that the MCP specification makes mandatory. The workaround is an
   audience mapper putting the MCP server's URL into the token's `aud` claim, which is
   static rather than derived from the request — fine for one resource server, which is
   what this is. Tracked upstream as keycloak#14355 and keycloak#41526.
2. **Serve Keycloak under the same hostname as the MCP endpoint.** Anthropic's own
   documentation says a cross-host authorization server is supported and explains the
   discovery handshake; a claude.ai issue reports the web client ignoring
   `authorization_endpoint`/`token_endpoint` and constructing `/authorize` and `/token`
   from the MCP base URL instead. The two disagree. Reverse-proxying Keycloak under one
   domain costs nothing and makes the disagreement moot, which is the right way to treat
   a contradiction you cannot resolve from outside.

**What it costs:** the elegant part of the previous plan dies. Keycloak authenticates
people to Keycloak; it knows nothing about Bitbucket API tokens. So the authorization page
can no longer *be* the credential page, and collecting the Atlassian token becomes a second
step — ticket 13, revived.

**What it buys, and this is the larger number:** nobody hand-rolls an authorization server
on a host holding the team's merge-capable credentials. Identity becomes real — disable
somebody in Keycloak and their access is gone, which beats the email-domain check below by
a distance. And the open-enrolment problem disappears entirely, because `/authorize`
belongs to Keycloak and Keycloak decides who may authenticate.

### What was not chosen, and what it would have cost

One login instead of two: the person's Atlassian email and API token, verified against
Bitbucket, *are* the authentication, and the authorization page is the credential page.
No second service to run, back up and patch.

The price is writing OAuth 2.1 correctly — PKCE S256, audience binding, refresh rotation
with `invalid_grant`, RFC 9207 `iss`, RFC 8414 metadata, a `/token` endpoint that parses
`application/x-www-form-urlencoded` and answers inside ten seconds without ever calling
Bitbucket. This is the most security-critical code in the project and the easiest place
to be subtly wrong.

It also leaves `/authorize` open to the internet with no answer to "is this person on the
team" beyond *the verified Bitbucket account's email domain*. That check is weak, and it
is the strongest one available on this path.

### Either way

- Register `https://claude.ai/api/mcp/auth_callback` as a redirect URI, for web, Desktop
  and mobile alike
- Prefer a **pre-registered client id and secret**, pasted once into Claude's Advanced
  settings, over Dynamic Client Registration — Anthropic recommends it for a connector
  scoped to one organisation, and it keeps an open registration endpoint off this host
- **Anthropic's requests come from `160.79.104.0/21`.** The MCP endpoint can be firewalled
  to that range, so the part holding credentials need not face the internet at all. Only
  the login and credential pages must, because a browser goes there

**Needs, to deploy but not to build:** a public HTTPS hostname with a real certificate.

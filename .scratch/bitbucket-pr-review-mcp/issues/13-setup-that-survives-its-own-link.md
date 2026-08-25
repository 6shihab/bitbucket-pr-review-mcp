# 13 — Collecting the Bitbucket credential

**Status:** done

This ticket began as a device-authorization flow — a URL *and* a short code shown in
chat — because a public setup link is no protection when whoever reads the transcript
first can open it and **supply** a credential: their token, stored as somebody else's, so
that person's review posts under the attacker's name.

The code turned out to be unnecessary, and the reason is worth keeping. With Keycloak as
the authorization server, the page that collects an Atlassian token sits **behind a
sign-in of its own**. The link stops being a capability. Somebody who reads it in a
transcript and opens it is asked who they are, and ends up connecting *their own* account
— which is not an attack, it is just them using the server. The fix came from the
transport, not from a longer secret.

That required the other half of OAuth: this server as an ordinary Keycloak *client*,
which is `oidc.py`. Authorization code with PKCE, one token exchange, one ID token
verified. Keycloak does the difficult half.

- [x] The credential page is unreachable without a Keycloak session, and a link opened by
      somebody else enrols *them* — asserted, not assumed
- [x] Verification against Bitbucket, with the display name shown back before anything is
      stored, and over-broad scopes refused at the form — the loopback page's behaviour,
      kept
- [x] The credential is stored against the signed-in person and nobody else, under the
      **same** vault key the access-token check computes — `person_id()` is defined once
      and both doors use it, with a test that they agree
- [x] CSRF-protected in its own right: a form token from the session cookie, plus Origin
      validation, not only OAuth `state`
- [x] Session cookies are signed, HttpOnly, `SameSite=Lax`, and `Secure` on https
- [x] The token never appears in a page, a redirect, a query string or a log line
- [x] An abandoned attempt leaves nothing stored — verification is held in memory for
      five minutes and is not another person's to save
- [x] Nothing a tool argument contains can start, extend or re-address it
- [x] Disconnecting is on the same page, and says the token still exists at Atlassian
      (which is most of ticket 15)

**Two things the tests pin because they would fail silently:**

`state` is checked **before** the code is exchanged, so a login somebody else began
cannot be completed in this browser. And a refused exchange does not repeat what Keycloak
said — that body can carry the authorization code, and the message ends up in a page.

**Verified live**, against the Keycloak in `docker-compose.shared.yaml`: an anonymous
visitor to `/connect` is redirected to Keycloak with PKCE `S256`, a `state`, and the right
`redirect_uri`; and a tool called without a credential now answers with the connect URL
and the honest note that the page will ask you to sign in.

---

**Driving it from Claude Desktop, added the same day.** A Claude Desktop *custom
connector* is reached from Anthropic's cloud, so `localhost` is unreachable no matter what
is configured. The local loop goes through `mcp-remote`, a stdio bridge that runs on the
person's own machine, does the OAuth flow there, and speaks HTTP to this server.

That needs a third Keycloak client, `bitbucket-pr-review-cli`, and it is **public** rather
than confidential on purpose: a client secret sitting in a config file on a laptop is not
a secret, and PKCE is what actually protects a loopback flow. Keycloak's registration
endpoint refuses anonymous callers, so the client is declared in the realm and named with
`--static-oauth-client-info`.

Verified by running the bridge: it discovers the authorization server from this server's
protected-resource metadata, asks for `bitbucket:review`, sends `resource=` (RFC 8707,
which Keycloak ignores and the audience mapper covers), and uses PKCE `S256`. It stops at
the Keycloak login page, which is a person's job.

One fact worth keeping: **mcp-remote's callback is `http://127.0.0.1:<port>/oauth/callback`
— the IP literal, not `localhost`, and `/oauth/callback`, not `/callback`.** Registering
only the `localhost` spelling, or only Claude Code's path, produces an
`invalid_redirect_uri` at the very last step.

# 13 — Collecting the Bitbucket credential

**Rewritten twice, and its size now depends on ticket 10B.**

This ticket began as a device-authorization flow: a URL *and* a short code shown in chat,
because a public setup link is no protection when whoever reads the transcript first can
open it and **supply** a credential — their token bound to the victim's session, so the
victim's review posts under the attacker's name.

## If the authorization server is ours

**This ticket nearly disappears.** Claude opens `/authorize` in the person's own browser as
part of connecting, and that page *is* the credential form. No link is relayed through a
tool answer, and the request carries `state` and a PKCE challenge that a transcript reader
does not have. What remains is the form's own protections, below.

## If the authorization server is Keycloak

**This ticket comes back, smaller and safer than its first version.** Keycloak knows
nothing about Bitbucket API tokens, so collecting one is a second step: a tool called
without a stored credential answers with a URL, and that page sits **behind the Keycloak
session**.

That is what makes the original attack impossible without a typed code. The link is no
longer a capability. Somebody who reads it and opens it is made to authenticate, and
whatever they enter is stored against *their own* account — not the session of the person
the link was meant for. The confused deputy needs a link that acts on behalf of whoever
holds it, and a page behind a login is not one.

Worth noticing: both designs solve it at the transport, and neither needs the six-digit
code this ticket was originally built around.

**Blocked by:** 10, 11, 12.

**Status:** ready-for-agent, once 10B is decided

- [ ] The existing `setup_app.py` form is reused: verification against Bitbucket, the
      display name shown back before anything is stored, over-broad scopes refused
- [ ] The credential is stored against the authenticated person and nobody else
- [ ] Served only over TLS, no CORS headers, Origin validated
- [ ] CSRF-protected in its own right, not only by OAuth `state`
- [ ] The token never appears in a page, a redirect, a query string or a log line
- [ ] An abandoned attempt leaves nothing stored
- [ ] Nothing a tool argument contains can start, extend or re-address it
- [ ] **If Keycloak:** the page is unreachable without a session, and a link opened by
      somebody else enrols *them*, which a test asserts rather than assumes

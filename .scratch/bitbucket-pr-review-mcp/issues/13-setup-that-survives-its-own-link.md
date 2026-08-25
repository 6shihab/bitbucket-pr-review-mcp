# 13 — The credential form, now that it is an OAuth consent page

**Rewritten 2026-08-25, and most of it was deleted.** This ticket used to be a
device-authorization flow: a URL *and* a short code shown in chat, because a public setup
link is useless protection when whoever reads the transcript first can open it and
**supply** a credential — their token bound to the victim's session, so the victim's
review posts under the attacker's name.

Deciding ticket 10 in favour of OAuth removes the premise. Claude opens `/authorize` in
the person's own browser as part of the connector flow. No link is relayed through a tool
answer, nothing is pasted into a chat, and the request carries `state` and a PKCE
challenge that a transcript reader does not have. The confused deputy has nowhere to
stand, and it is worth noticing that the fix came from the transport rather than from a
cleverer token.

What remains is the form itself, which mostly exists already in `setup_app.py`.

**Blocked by:** 10, 11, 12.

**Status:** ready-for-agent

- [ ] The existing setup page becomes the `/authorize` consent page, keeping its
      verification against Bitbucket and its display-name confirmation
- [ ] Over-broad scopes are refused at the form, as they are today
- [ ] It is served only over TLS, sets no CORS headers, and validates Origin
- [ ] The form is CSRF-protected in its own right, not only by OAuth `state`
- [ ] The token never appears in a page, a redirect, a query string or a log line
- [ ] An abandoned authorization leaves nothing stored
- [ ] Nothing a tool argument contains can start, extend or re-address an authorization

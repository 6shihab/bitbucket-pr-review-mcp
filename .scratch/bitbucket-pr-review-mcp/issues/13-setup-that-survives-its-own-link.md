# 13 — Setup that survives its own link being read

**What to build:** A Reviewer's first tool call answers with a URL **and a short code**.
The page will not take a credential without the code. Holding the link is not enough.

ADR-0004 accepted the setup link travelling through the model's transcript, and named the
reason: loopback-only, single-use, minutes. A public URL voids that acceptance, and the
attack it opens is not the obvious one. Whoever reads the link first cannot steal a
credential that is not there yet — they can **supply** one. Their token, bound to the
victim's session, so the victim's review posts under the attacker's account and every
comment lands with the attacker's name on it. A confused deputy made out of a URL.

A longer token does not fix that. Something the transcript reader does not have does, and
a code typed by the person is that.

**Blocked by:** 10, 11, 12.

**Status:** ready-for-agent

- [ ] A tool called without a Bitbucket credential answers with a URL and a code, both
      bound to the calling session
- [ ] The page refuses every credential until the right code is entered
- [ ] The code is single-use, expires within ten minutes, and five wrong attempts burn it
- [ ] A code from one session cannot complete setup for another
- [ ] The credential is verified against Bitbucket and the display name shown back before
      anything is stored, as it is today
- [ ] Over-broad scopes are refused at the form, as they are today
- [ ] The page sets no CORS headers, validates Origin, and is served only over TLS
- [ ] The code never appears in a log, and the token never appears in a page
- [ ] Nothing a tool argument contains can start, extend or re-address a setup session

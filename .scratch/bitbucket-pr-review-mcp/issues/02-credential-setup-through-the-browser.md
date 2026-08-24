# 02 — Credential setup through the browser

**What to build:** A Reviewer runs the server for the first time with no credential. Every
tool fails with an error that names a URL rather than a 401. They open it, land on a page
that tells them exactly which token scopes to tick and asks for their Atlassian account
email — by that name — and their API token. On submit the server verifies the pair against
Bitbucket and shows their own display name back before storing anything. The credential
goes into the OS keychain with the expiry date they entered, the page closes itself, and
every session afterwards is silent.

This replaces the environment-variable stopgap from ticket 01.

The listener exists only while the server holds no usable credential, per ADR-0004, and
re-opens only on observed facts — credential absent, past its stored expiry, or a real 401
from Bitbucket. It must never be summonable by a tool argument, because a Pull Request
description that could raise a credential form is a phishing vector aimed at a Reviewer
who is already expecting the tool to do things.

**Blocked by:** 01 — needs the HTTP client, settings and reference parsing.

**Status:** done — except one criterion, noted below

- [x] With no credential stored, every tool fails with an error naming the setup URL
- [x] The setup page names the exact token scopes to grant and links to where they are created
- [x] The field is labelled as the Atlassian account email, and a Bitbucket username is rejected rather than passed through to a later 401
- [x] Submitting verifies against Bitbucket and echoes the returned display name before anything is stored
- [x] A wrong email or token fails at the form, not on the next review
- [x] The credential is stored in the OS keychain and never written to a file
- [x] An unavailable keychain fails loudly rather than silently downgrading to file storage
- [x] The listener closes on first successful save, and after five minutes regardless
- [x] The listener binds loopback only, on a random port
- [x] A request carrying a foreign origin or host header is rejected
- [x] A wrong or already-used one-time token is rejected
- [x] The listener does not exist while a usable credential is stored
- [x] No tool argument can cause the listener to open
- [x] Startup warns when the stored token expires within seven days, naming the command that renews it
- [x] Startup refuses to run when the credential grants more than reading a repository and writing to Pull Requests
      **With one honest limit.** Scopes are read from Bitbucket's `x-oauth-scopes`
      response header, which is the only evidence available about an opaque token.
      When Bitbucket does not send that header the server cannot judge the token, and
      it starts with a warning rather than refusing — refusing on absent evidence
      would make the server unusable the day Atlassian changes a header. Anything
      unrecognised *in* the header is treated as excessive, so the check fails closed
      on scopes but open on silence.
- [x] The setup application is tested as an ASGI app in process, with real routing and real header validation
- [ ] Credential storage is tested against the keyring library's in-memory backend, not a bespoke abstraction
      **NOT MET as written.** keyring 25.7 ships no in-memory backend — only `fail`,
      `null`, `chainer` and the platform ones. `tests/memory_keyring.py` is the nearest
      honest reading: a real `keyring.backend.KeyringBackend` subclass installed with
      `keyring.set_keyring`, so `keychain.py` goes through the real keyring API and the
      seam is a backend rather than a wrapper of our own. It is still forty lines we
      wrote, so if `keyrings.alt` is ever added as a dev dependency its
      `PlaintextKeyring` (or a maintained in-memory backend) should replace it.

## What ticket 01 left open

Its last criterion — fixtures captured from real Bitbucket responses — is still unmet.
This ticket brings the credential that makes capture possible, but not a live account
or a real Pull Request, so `tests/fixtures.py` remains modelled on documented shapes.
The first Reviewer to run `--check` against a real workspace should record
`GET /2.0/user` and one pull request and replace those bodies.

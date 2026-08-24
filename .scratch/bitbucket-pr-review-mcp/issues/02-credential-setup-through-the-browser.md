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

**Status:** ready-for-agent

- [ ] With no credential stored, every tool fails with an error naming the setup URL
- [ ] The setup page names the exact token scopes to grant and links to where they are created
- [ ] The field is labelled as the Atlassian account email, and a Bitbucket username is rejected rather than passed through to a later 401
- [ ] Submitting verifies against Bitbucket and echoes the returned display name before anything is stored
- [ ] A wrong email or token fails at the form, not on the next review
- [ ] The credential is stored in the OS keychain and never written to a file
- [ ] An unavailable keychain fails loudly rather than silently downgrading to file storage
- [ ] The listener closes on first successful save, and after five minutes regardless
- [ ] The listener binds loopback only, on a random port
- [ ] A request carrying a foreign origin or host header is rejected
- [ ] A wrong or already-used one-time token is rejected
- [ ] The listener does not exist while a usable credential is stored
- [ ] No tool argument can cause the listener to open
- [ ] Startup warns when the stored token expires within seven days, naming the command that renews it
- [ ] Startup refuses to run when the credential grants more than reading a repository and writing to Pull Requests
- [ ] The setup application is tested as an ASGI app in process, with real routing and real header validation
- [ ] Credential storage is tested against the keyring library's in-memory backend, not a bespoke abstraction

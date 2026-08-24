# 10 — Callers say who they are

**What to build:** Every MCP session arriving at the shared server identifies the person
it acts for, and a session that cannot is refused before it reaches a tool. An Operator
enrols a person with one command and hands them a client credential; revoking it takes
one command and takes effect immediately.

This is first because nothing else is designable without it. "Which Bitbucket credential
does this tool call use?" has no answer until the server knows whose call it is, and every
later ticket — the store, the setup flow, attribution, deletion — is a sentence that
starts "for this person".

The client credential is **not** the Bitbucket credential, and the two must never be
confused in code, in logs or in prose. It identifies a person to this server. Losing it
lets somebody use that person's Bitbucket access *through* this server; it does not hand
them the token itself, and that distinction is the difference between an incident and a
catastrophe.

**Blocked by:** Nothing — but see the spec's first assumption. If the target clients are
Claude Desktop and Claude Code, this becomes an OAuth implementation rather than a bearer
token, and that decision belongs before the first line of code.

**Status:** needs-decision

- [ ] An Operator can enrol a person and receive a client credential to hand them, once
- [ ] The credential is stored hashed, never in plaintext, and never logged
- [ ] Every request carries it, and one that does not is refused before any tool runs
- [ ] A revoked enrolment stops working on the next request, not at the next restart
- [ ] The refusal says what to do without revealing whether that person is enrolled
- [ ] Enrolment and revocation are commands, never tools
- [ ] The stdio server keeps working with no authentication, because there is one caller
- [ ] Every log line about a session names the person, never the credential

# 16 — Operating a thing that holds credentials

**What to build:** What an Operator needs to run this without becoming the incident: a
health command, bounded logging, backups that are useless on their own, and a written
statement of what one compromise of this host costs.

That last item is not documentation garnish. ADR-0002 is blunt that every credential here
can merge, because Bitbucket sells no comment-only permission. On a laptop that is one
person's exposure. On a shared server it is every enrolled person's, from one compromise,
at once — and the only mechanism that survives it is branch restrictions on the
repositories themselves. An Operator who has not read that sentence should not be running
this.

**Blocked by:** 11, 12.

**Status:** ready-for-agent

- [ ] One command reports TLS, store reachable, key present, allowlist loaded, and how
      many enrolments and credentials are live — exiting with a meaningful shell status
- [ ] Logs never contain a Bitbucket token, a client credential, a setup code or a pull
      request body, asserted by a test that goes looking for them
- [ ] Logs are structured and safe to ship to a central aggregator
- [ ] A backup of the store is useless without the key, and the documentation says how to
      back up the key separately
- [ ] Key rotation is possible without re-enrolling anybody
- [ ] The deployment document states what one compromise costs, in those words, and names
      branch restrictions as the mechanism that survives it
- [ ] The architecture document gains the shared deployment, and ADR-0003 and ADR-0004
      each gain a note saying what the shared server does instead, and why

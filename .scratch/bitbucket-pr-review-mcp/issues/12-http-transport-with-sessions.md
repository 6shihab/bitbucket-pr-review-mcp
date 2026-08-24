# 12 — HTTP transport, with sessions

**What to build:** The same eleven tools, over streamable HTTP, with a session bound to
the authenticated person — and stdio still working exactly as it does today.

Two deployment shapes, one review engine. The per-device install remains the right answer
for one person and must not regress to serve a team: it holds one credential and can be
destroyed by deleting a keychain entry, which is a property worth keeping.

The work is mostly *not* in the tools. It is in what used to be process-wide and now has
to be per-session, and in proving that a request for one person can never be served with
another person's state.

**Blocked by:** 10, 11.

**Status:** ready-for-agent

- [ ] Every tool behaves identically over HTTP and over stdio, asserted by the same tests
      running against both
- [ ] A session resolves to exactly one person, for its whole life
- [ ] `CredentialGate` and `KnownIdentity` are per person; nothing process-wide holds
      either
- [ ] Concurrent sessions for different people cannot see each other's credential,
      identity or in-flight state, under a test that runs them at once
- [ ] The diff cache is shared, and the reason that is safe is argued in a comment where
      it is built — the allowlist bounds every person identically
- [ ] TLS is required: the server refuses to start without it, as it refuses an absent
      allowlist
- [ ] stdio needs no authentication and no TLS, and that difference is deliberate and
      documented

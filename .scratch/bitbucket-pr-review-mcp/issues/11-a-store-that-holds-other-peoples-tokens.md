# 11 — A store that holds other people's tokens

**What to build:** Per-person Bitbucket credentials, encrypted at rest in a store the
server can read and a stolen disk cannot. The threat model is written **before** the code,
because "encrypted at rest" is a phrase that hides every question worth asking.

ADR-0003 says the keychain and nothing else, and refuses to downgrade. That decision was
about a per-device tool: a keychain holds one credential for the person running the
process, which is exactly wrong for holding credentials on behalf of others. This ticket
reverses it for the shared deployment only. The per-device server keeps its keychain.

The honest framing, and it belongs in the ADR: this is the ticket that turns the project
into a credential vault. Everything else is plumbing around it.

**Blocked by:** 10 — a credential is stored against a person.

**Status:** ready-for-agent

- [ ] The threat model is written first and covers: the database file alone; the file plus
      a copy of the environment; an operator with shell access; a compromise of the
      running process; and a backup. For each, exactly what an attacker gets
- [ ] Each credential is encrypted with an AEAD, under a key supplied at startup
- [ ] The key never enters the database, the logs, or an error message
- [ ] The server refuses to start if the key is missing, rather than generating one
- [ ] A stored credential can be read back, re-encrypted under a new key, and deleted
- [ ] Two people's credentials are never interchangeable, even by a bug in one query
- [ ] Storage is tested against a real database file, not a fake
- [ ] Nothing in the store's public surface hands a token to anything but the HTTP client
      that uses it

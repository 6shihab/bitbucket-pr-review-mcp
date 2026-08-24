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

**Blocked by:** 10 — a credential is stored against a person. Built ahead of it against an
opaque enrolment id, because how a person is authenticated does not change how their
credential is stored, and the store is what every later ticket needs.

**Status:** done

- [x] The threat model is written first and covers: the database file alone; the file plus
      a copy of the environment; an operator with shell access; a compromise of the
      running process; and a backup. For each, exactly what an attacker gets
      — `docs/threat-model-shared-store.md`, seven adversaries
- [x] Each credential is encrypted with an AEAD, under a key supplied at startup
      — AES-GCM, key from `BB_MCP_VAULT_KEY` or `BB_MCP_VAULT_KEY_FILE`
- [x] The key never enters the database, the logs, or an error message
- [x] The server refuses to start if the key is missing, rather than generating one
      — `VaultKey.required()`, and the refusal says why it will not invent one
- [x] A stored credential can be read back, re-encrypted under a new key, and deleted
      — `load`, `rotate`, `clear`; rotation re-seals every row and nobody re-enrols
- [x] Two people's credentials are never interchangeable, even by a bug in one query
      — the person's id is AEAD associated data; a row moved between people fails to
      decrypt rather than being handed to the wrong person
- [x] Storage is tested against a real database file, not a fake
      — every test in `tests/test_vault.py` writes a real SQLite file, and three of them
      open the raw bytes looking for the token
- [x] Nothing in the store's public surface hands a token to anything but the HTTP client
      that uses it — `PersonalCredentials` satisfies the existing `CredentialStore`
      protocol, so `CredentialGate` works over vault or keychain unchanged

**Left for later, deliberately:** the vault is not wired into a running server yet. Which
person a session means is ticket 10's answer, and the HTTP transport that carries sessions
is ticket 12. Nothing imports `vault.py` outside its tests, which is the correct amount of
coupling until then.

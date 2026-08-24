# Threat model: the shared credential store

This document exists because "encrypted at rest" is a phrase that hides every question
worth asking. It is written **before** the store it describes, and it is the reason
[ADR-0008](adr/0008-the-shared-server-holds-other-peoples-credentials.md) exists.

## What is being protected

Atlassian API tokens belonging to several people, each of which can **merge pull requests
in every allowlisted repository**. Bitbucket sells no permission that separates commenting
from merging ([ADR-0002](adr/0002-comment-only-blast-radius.md)), so there is no weaker
version of this asset to hold instead. A token here is not "read access to some diffs". It
is its owner's write access to the team's default branches.

Also stored, and worth naming separately: each person's Atlassian account email, the date
they connected, and the expiry they typed. Those are not secrets, but a list of them is a
list of who has merge rights to what.

## What is not being protected here

- **The repositories themselves.** One allowlist bounds the whole deployment. Nothing in
  this store narrows what any credential may reach.
- **Bitbucket's own access control.** Branch restrictions are configured on the
  repository and are enforced by Bitbucket regardless of anything below.
- **The client credential** that identifies a caller to this server. That is a different
  secret with a different lifetime, covered by ticket 10.

## Design, stated plainly

One SQLite file. One row per enrolled person. Each row holds the person's opaque
enrolment id and the time the row was written in clear, and **everything else — email,
token, expiry — inside a single AEAD ciphertext**. The key is supplied at startup and
never written to the database.

The person's id is bound into the ciphertext as associated data. Moving a row between
people, by hand or by a bug in a query, produces a decryption failure rather than a
credential belonging to somebody else. That is the one property worth buying with
cryptography here; confidentiality against a stolen file is the other.

## Adversaries, and exactly what each one gets

### 1. Someone with the database file, and nothing else

A copied disk, a stolen backup, a snapshot in an object store with the wrong ACL.

**They get:** the number of enrolled people, the opaque id of each, and when each row was
written. **They do not get:** any token, any email, any expiry.

This is the case the encryption is for, and it is the only adversary the encryption
defeats outright. It is worth being clear that this is a narrow win: it defeats the
careless copy, not the attacker who is already inside.

### 2. Someone with the file *and* the environment

A process listing that shows the key, an `docker inspect`, a leaked CI variable, a backup
that captured the whole container.

**They get: every token, for every enrolled person.** The encryption buys nothing here.

This is why the key must not live beside the data — not in the database, not in the same
volume, not in the same backup. Where it does live is a deployment decision, and the
deployment document must state it rather than leaving it to whoever runs the image.

### 3. An operator with shell access on the host

**They get: everything, whenever they want it.** They can read the key, read the file,
attach a debugger, or simply ask the running server to use a credential.

Nothing in this codebase can change that, and pretending otherwise would be worse than
saying it. The controls that apply are organisational and external: who has shell on this
host, audit of that access, and branch restrictions that make merge access require a
review rather than a token.

### 4. A compromise of the running process

A dependency with a backdoor, a remote code execution in the HTTP surface, a malicious
container image.

**They get: every token, for every enrolled person, at once.** The key is in memory
because the process needs it, and every row is decryptable with it.

Two things bound this and neither is cryptography. Keeping the enrolled set small bounds
how much one compromise is worth. Short token expiries bound how long it stays worth it.
Both are policy, and both belong in the deployment document.

### 5. A backup of the store

**They get** what adversary 1 gets, provided the backup does not also contain the key.
Making that true is the whole requirement: back the key up separately, to somewhere the
store's backups do not reach, and test restoring both.

### 6. A caller of the MCP server, authenticated as somebody else

Not a store threat in itself, but the reason the store's shape matters. The store never
answers "give me a credential" — it answers "give me *this person's* credential", and the
person comes from the authenticated session, never from a tool argument. The associated
data check means a mistake in that lookup fails loudly instead of quietly returning the
wrong person's token.

### 7. The language model

The token reaches an `Authorization` header and nothing else. It is never a tool
argument, never in a tool's answer, never in an error, and never in a log line. That
property is asserted by `tests/test_the_token_never_reaches_the_model.py` and is not
weakened by this store — the vault's public surface hands out a `Credential`, whose
`__repr__` is redacted, and the only consumer is the HTTP client.

## What this store is strictly worse at than the per-device one

The per-device server keeps its OS keychain ([ADR-0003](adr/0003-atlassian-api-token-not-oauth.md)),
and that is not sentiment. A keychain is a separate process with its own access control,
often backed by hardware, that can require the human to be present. This store is a file
that the server can read whenever it likes, protected by a key the server also holds.

It is chosen because a keychain holds one credential for the person running the process,
which is exactly the wrong shape for holding credentials on behalf of other people. It is
a downgrade accepted for a reason, not an equivalent alternative, and the per-device
install remains the right answer for one person.

## Requirements this produces

These are the things the code must do, and they are the acceptance criteria of ticket 11:

1. Each credential is sealed with an AEAD under a key supplied at startup.
2. The person's id is authenticated as associated data, so rows are not interchangeable.
3. The key never enters the database, a log line, or an error message.
4. A missing key stops the server. Nothing generates one, because a generated key is a
   key nobody backed up, and a key nobody backed up is every credential lost on restart.
5. Rotation re-encrypts every row under a new key without anybody re-enrolling.
6. Deletion removes the row.
7. The file is created with owner-only permissions where the platform has them.
8. A test opens the raw file and fails if a token can be found in it.

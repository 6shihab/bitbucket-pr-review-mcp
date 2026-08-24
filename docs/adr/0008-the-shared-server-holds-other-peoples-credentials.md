# The shared server holds other people's credentials, in a file it can read

ADR-0003 said the OS keychain and nothing else, and said the server should refuse to run
rather than downgrade to a file. The shared deployment downgrades to a file. This records
why, and what was given up.

A keychain holds one credential, for the person running the process, unlocked by that
person being there. That is the right shape for a tool on a laptop and the wrong shape for
a service that must use several people's credentials while none of them are present. There
is no keychain arrangement that stores a colleague's token on a server they have never
logged into.

So the shared server stores credentials itself: one SQLite file, one row per enrolled
person, each row sealed with an AEAD under a key supplied at startup and never written
beside the data. The person's enrolment id is authenticated as associated data, so a row
cannot be read as somebody else's — a bug in a lookup fails to decrypt rather than
returning the wrong person's token.

The honest description of what this makes the project is in
[the threat model](../threat-model-shared-store.md), and it is not flattering: **a
credential vault holding merge access to every allowlisted repository, for everybody
enrolled**. The encryption defeats one adversary — somebody who gets the file and not the
key. It defeats none of the others. An operator with shell, or anything that compromises
the running process, gets every token at once, and no arrangement of ciphers changes that.

We considered three alternatives.

**Per-person keychains on the server** do not exist. A keychain belongs to a logged-in
session; a service account has one keychain, which is the file problem with extra steps.

**A secrets manager** (Vault, AWS Secrets Manager, KMS-wrapped rows) is genuinely better
and is not excluded — the key is supplied at startup by whatever the deployment has, so a
KMS-provided key is a configuration choice rather than a code change. What was rejected is
*requiring* one, because it makes the thing undeployable for the team that wants it.

**Not storing tokens at all** — asking each person for their token per session — was the
alternative we would have preferred, and Bitbucket does not permit it. ADR-0003 records
that Bitbucket Cloud has no PKCE, so there is no browser flow that ends in a short-lived
per-session token. The credential is long-lived or it does not exist.

## Consequences

The server refuses to start without a key. It does not generate one: a generated key is a
key nobody backed up, and a key nobody backed up is every credential lost on the next
restart, silently, which is the worst property a failure can have.

Key rotation re-encrypts every row without anybody re-enrolling, so a suspected key
exposure is an operation rather than an onboarding exercise.

Two mechanisms now carry the weight that the keychain used to: **branch restrictions**,
which survive this host being compromised entirely, and **short token expiries**, which
bound how long a stolen set stays useful. Both are the operator's to configure, and the
deployment document says so in those words.

The per-device server keeps its keychain and keeps ADR-0003 intact. Two deployment shapes,
one review engine, and the per-device one is still what an individual should install.

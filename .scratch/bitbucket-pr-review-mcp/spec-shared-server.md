# Bitbucket PR Review MCP — the shared server

Status: draft, three decisions outstanding (see [Assumptions](#assumptions))

## Problem Statement

The server built in tickets 01–09 is a per-device tool: one process, one credential in
one OS keychain, one Reviewer, spoken to over stdio. Getting a team onto it means every
person installing it and running `--setup` once.

The team wants one deployment instead — several people pointing their MCP clients at a
single server, with the credential page reachable over the internet so a Reviewer can
open it from wherever they are, rather than only from the machine the process runs on.

That is a larger change than it sounds, and the reason is worth stating before any of the
work: **a shared server holding several people's Bitbucket credentials is a credential
vault.** Not a tool that happens to hold a token — a service whose primary asset is other
people's access to their own repositories. Every decision below follows from that, and
two of the existing ADRs are reversed by it.

The sharper worry from the original spec gets sharper. Bitbucket sells no permission
that separates commenting from merging (ADR-0002), so every credential this thing holds
can merge. On a laptop that is one person's token and one person's exposure. On a shared
host it is everyone's, at once, from a single compromise.

## Solution

The same eleven tools, the same guard, the same review machinery — reached over an
authenticated HTTP transport instead of stdio, with per-person credentials in an
encrypted store instead of a keychain, and a setup flow that survives its own link being
read by somebody else.

Four things are new. Everything else is what already exists.

**1. Callers authenticate.** Every MCP session says who it is for. Without this, "which
credential does this tool call use?" has no answer and nothing else in this document is
designable. This is the first ticket, not an afterthought.

**2. Credentials live in a store, per person.** SQLite, encrypted per record, with a key
that does not live in the database. Weaker than a keychain, and documented as such with a
written threat model rather than the phrase "encrypted at rest".

**3. Setup is a device-code flow, not a link.** The tool answer carries a URL *and a short
code shown in the chat*. The page will not accept a credential without the code. Holding
the link is not enough, which is exactly the property the loopback listener used to get
for free.

**4. Deletion and enrolment are first-class.** A person can remove their own credential; an
operator can see who is enrolled and revoke an enrolment. Neither is a tool.

## User Stories

### Enrolling and connecting

1. As an Operator, I want to enrol a person and hand them a client credential, so that
   their MCP client can identify itself to the server.
2. As a Reviewer, I want to point my MCP client at the shared server with the credential
   I was given, so that I can use it without installing anything.
3. As a Reviewer, on my first tool call I want to be told where to connect my Bitbucket
   account **and given a short code**, so that reading the link alone does not let anyone
   act for me.
4. As a Reviewer, I want the setup page to refuse my credential unless the code matches,
   so that a link seen in a transcript, a log or a screenshot is useless on its own.
5. As a Reviewer, I want the page to verify my token against Bitbucket and show me my own
   display name before storing anything, so that a token pasted from the wrong account is
   caught by me rather than by a later 401.
6. As a Reviewer, I want my credential bound to *my* session only, so that no one else's
   review ever posts under my name and mine never posts under theirs.
7. As a Reviewer, I want to remove my credential from the server without asking an
   operator, so that leaving is as easy as joining.
8. As an Operator, I want to revoke somebody's enrolment and have their credential deleted
   with it, so that offboarding is one action.

### Reviewing, unchanged

9. As a Caller, I want every tool that worked over stdio to work identically here, so that
   the shared server is a deployment choice rather than a different product.
10. As a Reviewer, I want my comments attributed to my Bitbucket account and nobody
    else's, so that accountability survives the server being shared.
11. As a Caller, I want a diff cache shared between people to be safe, or not shared at
    all, so that convenience never leaks one repository's content into another's review.

### Containment

12. As a Reviewer, I want the server to refuse to start without TLS, so that no
    deployment ever carries an API token over plain HTTP by accident.
13. As an Operator, I want the server to refuse to start without an allowlist, exactly as
    the per-device one does, so that a shared deployment cannot reach further than a
    laptop could.
14. As an Operator, I want to know what one compromise of this host costs, in writing,
    before I run it.
15. As an Operator, I want the logs to be safe to ship to a log aggregator, so that
    diagnosing a problem does not become the incident.

### Operating

16. As an Operator, I want a single command that says whether the deployment is healthy:
    TLS, store, key, allowlist, and how many enrolments are live.
17. As an Operator, I want backups of the store to be useless without the key, so that a
    leaked backup is not a leaked credential set.
18. As a Reviewer, I want to be warned before my token expires, as I am today.

## Implementation Decisions

### What survives untouched

The guard, the allowlist, the diff parser and cache, anchoring, batch validation,
duplicate detection, the summary comment, the untrusted-content fence, and every response
reader. None of it cares how many people are using it, and none of it should change.

The layering is what makes that true: only `server.py` knows about the protocol, and only
`client.py` talks to Bitbucket. The new work sits either side of that spine — transport
and identity above, credential storage below — and the middle is left alone.

### What must become per-person

| Today | Shared |
|---|---|
| `Keychain` — one credential | `CredentialStore` — one per enrolled person |
| `CredentialGate` — one, process-wide | One per session, resolved from the caller's identity |
| `KnownIdentity` — caches "who we are" | Per person. Sharing it attributes one person's comments to another |
| `SetupListener` — loopback, random port | A route on the public app, with a code |
| `DiffCache` — process-wide | Shared is safe *only* because its contents are bounded by the same allowlist for everyone. If per-repository permissions are ever added, this must be re-keyed |

That last row is the kind of thing that is obvious now and invisible in a year, so it
belongs in a comment where the cache is built, not only here.

### Authentication of Callers

A bearer token per enrolled person, issued by an operator command, presented by the MCP
client on every request. Chosen over OAuth against an identity provider because it works
with any client that can set a header and needs nothing else deployed — but see
[Assumptions](#assumptions): if the target clients are Claude Desktop and Claude Code
specifically, their remote-MCP support expects OAuth, and that changes this decision.

The bearer token identifies a person, not a device, and it is not the Bitbucket
credential. Losing it lets somebody use that person's Bitbucket access through this
server; it does not hand them the token itself.

### The setup flow

1. A tool called without a Bitbucket credential answers with a URL **and a six-character
   code**, both bound to the calling session.
2. The Reviewer opens the URL. The page asks for the code first.
3. With the right code, it asks for the Atlassian account email and API token, verifies
   them against Bitbucket, and shows the returned display name back.
4. On confirmation the credential is encrypted and stored against that person.
5. The code is single-use, expires in ten minutes, and is rate-limited: five wrong
   attempts burn it.

The code is the whole point. ADR-0004 accepted the setup link travelling through the
model's transcript because the link was loopback-only; a public link needs something the
transcript reader does not have, and a code typed by the person is that.

Without it the attack is not "somebody steals a credential" — it is "somebody *supplies*
one": their token, bound to the victim's session, so the victim's review posts under the
attacker's account. A confused deputy made out of a URL.

### The store

SQLite, one file. Each credential encrypted with an AEAD (XChaCha20-Poly1305 or
AES-GCM), key derived from a secret supplied at startup — environment variable, mounted
file, or KMS — never stored in the database and never logged.

The threat model gets written before the code, and covers at least: an attacker with the
database file; an attacker with the file *and* a backup of the environment; an operator
with shell access; a compromise of the running process; and what each of those means for
every enrolled person. The answer to the last one is "everything", and saying so is the
point of writing it down.

### Transport

MCP over streamable HTTP, sessions keyed to the authenticated person. stdio stays
supported and unchanged, because the per-device install is still the right answer for one
person and should not be broken to serve a team.

TLS is required and checked at startup — refusing to run without it, the way the server
already refuses to run without an allowlist. A misconfiguration that carries API tokens
in clear text should be impossible to deploy rather than easy to miss.

### Logging

Structured, and explicitly bounded: never the Bitbucket token, never the bearer token,
never a setup code, never a full pull request body. A person's email and account id are
in scope; their code and credentials are not. The logs should be safe to ship somewhere
central, and there should be a test asserting that.

## Assumptions

Three decisions are not made here and change the work materially:

1. **Which MCP clients matter.** Bearer tokens work everywhere but are unusual for remote
   MCP; Claude Desktop and Claude Code expect OAuth for remote servers. If those are the
   targets, ticket 1 becomes an OAuth implementation.
2. **Where it runs, and what is already there.** An existing identity provider, a
   secrets manager, or a Kubernetes cluster each change the answers about keys and TLS.
3. **How many people, and are they one team?** Ten colleagues on one allowlist is a
   different risk calculation from fifty across several teams — and the second probably
   wants per-person repository scoping, which this spec does not include.

## Out of Scope

- Per-person repository allowlists. One allowlist bounds the whole deployment, as today.
- Any credential type other than an Atlassian API token (ADR-0003 still holds).
- Reviewing anything but Bitbucket Cloud pull requests.
- A web interface beyond enrolment, setup and deletion. This is not becoming a product.
- Making the model smarter. It still forms the review; this still only carries it.

## Further Notes

The per-device server should keep working, keep its keychain, and keep its loopback
listener. Two deployment shapes, one review engine — and the per-device one remains the
one to recommend for an individual, because it holds one credential and can be destroyed
by deleting a keychain entry.

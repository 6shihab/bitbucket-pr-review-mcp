# A shared server with a public setup page — what it costs and what it needs

Decided 2026-08-24: the server should run once and serve several people, and the
credential page should be reachable over the internet so a Reviewer can open it from
wherever they are.

This is not a feature on top of what exists. It reverses ADR-0003 and ADR-0004, and it
changes the thing being built from *a tool on your laptop* into *a service holding other
people's Bitbucket credentials*. That sentence is the decision. Everything below follows
from it.

## What changes, and why each one is forced

### 1. The server stops being one Reviewer's

Today the server is one process, one credential, one identity, spoken to over stdio by
one client. Almost every piece of state assumes that:

| Today | Shared |
|---|---|
| One credential in the OS keychain | N credentials, one per person, in a store the process can read |
| `KnownIdentity` caches "who we are" per process | Per person — sharing it would attribute one person's comments to another |
| stdio: one client per process | HTTP transport with sessions, because several clients connect at once |
| No user identity anywhere | Every request must say who it is for, or nothing else here is answerable |

The last row is the one that has no workaround. "Which credential should this tool call
use?" is unanswerable unless the server knows who is calling, so **the MCP client has to
authenticate to the server** before any of the rest can be designed. That is a new
authentication system, and it is the first ticket, not an afterthought.

### 2. The keychain cannot be the store

ADR-0003 says: OS keychain, never a file, refuse rather than downgrade. That decision was
about a per-device tool, and a keychain holds one credential for the user running the
process — not N credentials on behalf of other people.

So the store becomes a database, encrypted at rest, with a key that lives somewhere the
database does not. That is strictly weaker than a keychain and needs its own threat
model: who can read the disk, who can read the key, what a backup contains, what an
operator can see, and what happens when the process is compromised. **Write that down
before writing the code**, because "encrypted at rest" is a phrase that hides all of it.

### 3. The public setup link becomes exploitable, and the current design says so

ADR-0004 accepted that the one-time setup token travels through the model's transcript,
and gave the reason: *it is loopback-only, single-use, and expires in minutes*. Take away
loopback and the acceptance is void. On a public URL, anyone who reads that link before
the Reviewer does — from a transcript, a log, a proxy, a screenshot in a ticket — can
open the page.

What they can do with it is worse than it first looks. They cannot read a credential that
is not there yet. They can **put one in**: their own token, bound to the victim's session.
The victim then reviews pull requests using the attacker's account, and every comment
they post lands under the attacker's name — or, if the attacker's token has wider scopes,
the victim's review activity does things nobody intended. A confused deputy, made out of
a link.

**The fix is not a longer token.** It is that possessing the link must not be enough:

- The page requires a **short code shown in the chat** that the Reviewer types in — the
  device-authorization shape. Interception of the URL alone then achieves nothing, and
  the code is entered by the person, not by anything that read the link.
- The setup session is **bound to the MCP session** that asked for it, so a credential
  entered through one link can only ever land on the session that requested it.
- It is **rate-limited and single-use**, and the code expires in minutes.
- TLS is mandatory. Without it the form POST carries an API token in clear text.

### 4. The blast radius of one compromise multiplies

ADR-0002 is blunt that every credential here can merge, because Bitbucket sells no
comment-only permission. On a laptop that is one person's token. On a shared server it is
everyone's — so a compromise of that host is merge access to every allowlisted repository
for every enrolled person, at once.

Nothing in this codebase can reduce that. What can: branch restrictions (already the
README's prerequisite, and now the *main* protection rather than a belt-and-braces one),
short token expiries, and keeping the enrolled set small. A shared server should probably
also refuse to start without TLS and without an allowlist, the way it already refuses an
absent repository list.

## What survives unchanged

Most of the actual review machinery, which is the good news. The guard, the allowlist, the
diff cache, anchoring, batch validation, dedup, the summary comment, the untrusted-content
fence — none of that cares how many people are using it. The changes are concentrated in
the credential, the identity and the transport.

One caveat: `KnownIdentity` and anything else cached per process must be re-keyed per
user, and the diff cache is safe to share only because its contents are already bounded
by the allowlist. Each per-process cache needs that argument made explicitly.

## Suggested order

1. **Client authentication.** Nothing else is designable until "who is calling" has an
   answer. Decide: bearer tokens issued at enrolment, or OAuth against an identity
   provider you already run.
2. **Per-user credential store**, with the threat model written first.
3. **HTTP transport with sessions**, replacing stdio (or offered alongside it).
4. **The device-code setup flow**: link plus a code shown in chat, bound to a session,
   over TLS.
5. **Per-user identity and attribution** — re-key `KnownIdentity`, and make sure a
   comment can never be attributed to the wrong person.
6. **Per-user deletion and enrolment removal**, and an operator view of who is enrolled.
7. **Deployment**: TLS termination, secret management, backups that do not leak the
   store, and what the logs are allowed to contain.

## The question worth asking first

Is a shared server actually what is wanted, or is it a way to avoid asking each person to
run `--setup` once? If it is the second, the cheaper answer is the current design plus a
better first-run experience — because the shared server costs an authentication system, a
credential vault, a threat model, and a much larger thing to lose.

If it is genuinely wanted — several people, one deployment, central control — then the
above is the work, and it is a project rather than a change.

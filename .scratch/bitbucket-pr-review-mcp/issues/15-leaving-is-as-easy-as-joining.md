# 15 — Leaving is as easy as joining

**Status:** done

- [x] A Reviewer can delete their own credential from an authenticated page — ticket 13,
      on the same page they connected it
- [x] Deleting it does not remove their enrolment: they can connect a new token
- [x] An Operator can revoke somebody's stored credential — `bb-pr-mcp --revoke`, by
      email or by enough of the opaque id, refusing rather than guessing when a name
      matches two people
- [x] Both say plainly that the token still exists at Atlassian, and where to revoke it
- [x] Neither is reachable as a tool, and no tool argument can trigger either
- [x] An Operator can list who is enrolled, when they connected, and when their token
      expires — without seeing any token
- [x] Deletion is immediate: the next tool call for that person asks them to connect again

## The word "enrolment" turned out to be wrong

The ticket asked for revoking an *enrolment*, and this server does not own one. With
Keycloak deciding who may sign in, three different places hold something after somebody
leaves:

| Where | What | Who can remove it |
|---|---|---|
| Here | Their stored Bitbucket credential | `--revoke`, or they can, on the connect page |
| Keycloak | Their ability to sign in at all | An operator, in Keycloak |
| Atlassian | The API token itself, everywhere it works | They, or an Atlassian admin |

`--revoke` owns exactly one row of that table and says so every time it runs. A command
that implied otherwise would get an offboarding checklist ticked while the person still
had access, which is a worse failure than having no command at all.

## The bug this ticket found

`--revoke` runs in a **different process** from the server. Nothing can reach across that
boundary to invalidate a cached session — so a revoked credential kept working until the
server was restarted, which is the one thing revocation must not do.

Ticket 14's callback does not help here; it is in-process by construction. The fix is that
the shared server's gate no longer holds the credential at all. `CredentialGate(hold=False)`
reads it through on every call, and notices when it has been replaced or removed by
anything, in any process.

The per-device gate still holds, and the comment saying why has been kept: on macOS every
keychain read is a potential prompt, and a tool call that asks the Reviewer to authorise
something is a habit worth not building. A SQLite row is neither slow nor a prompt, so the
reason for caching does not survive the move to a shared server — which is the sort of
thing worth re-deciding rather than inheriting.

Confirmed by setting `hold=True` and watching both cross-process tests fail.

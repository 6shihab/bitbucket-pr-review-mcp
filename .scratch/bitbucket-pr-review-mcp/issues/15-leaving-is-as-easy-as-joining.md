# 15 — Leaving is as easy as joining

**What to build:** A Reviewer removes their own Bitbucket credential from the server
without asking anyone. An Operator revokes an enrolment and the credential goes with it.
Both say what they have *not* done: the token still exists at Atlassian until it is
revoked there.

The per-device server does this with `--forget`, deliberately a command rather than a
tool, because a tool that deletes a credential is a tool a pull request description can
talk a Caller into calling. The same reasoning holds here, but the person is no longer at
the terminal — so self-service deletion is a page behind the same authentication. Still
not a tool.

**Blocked by:** 10, 11.

**Status:** ready-for-agent

- [ ] A Reviewer can delete their own credential from an authenticated page
- [ ] Deleting it does not remove their enrolment: they can connect a new token
- [ ] An Operator can revoke an enrolment, and the credential is deleted with it
- [ ] Both say plainly that the token still exists at Atlassian, and where to revoke it
- [ ] Neither is reachable as a tool, and no tool argument can trigger either
- [ ] An Operator can list who is enrolled, when they connected, and when their token
      expires — without seeing any token
- [ ] Deletion is immediate: the next tool call for that person asks for setup again

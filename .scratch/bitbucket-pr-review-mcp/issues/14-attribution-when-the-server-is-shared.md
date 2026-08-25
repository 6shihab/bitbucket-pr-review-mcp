# 14 — Attribution when the server is shared

**What to build:** Every comment posts under the Bitbucket account of the person whose
session asked for it, and the Attribution Footer names them. A shared server must never
attribute one person's review to another.

**Status:** done

Ticket 05 established that "is this comment ours?" is answered from the author's account
id, cached per process by `KnownIdentity`. Ticket 12 made that cache per person, which
covered most of this ticket before it started. What was left was the half nobody would
have found by reading the criteria.

- [x] `KnownIdentity` is per person, and one session cannot read another's — ticket 12
- [x] The Attribution Footer names the Reviewer whose credential posted the comment
- [x] "Ours" means "this person's", not "this server's", everywhere it is decided —
      each person asks Bitbucket who *they* are, once, and the answers do not mingle
- [x] The canonical Summary Comment is found and updated per person — `canonical()`
      already required both the marker *and* being ours, and "ours" is now per person;
      `_verified` still re-reads authorship before any PUT
- [x] Two people reviewing the same pull request at once produce two correct sets of
      comments — a test runs ten calls concurrently and checks which credential reached
      Bitbucket for each
- [x] A person's identity is forgotten when their credential is removed

## The bug this ticket actually found

The connect page writes **straight to the vault**. Nothing told the sessions. So:

* somebody who disconnected their account kept posting, because their session still held
  the credential it had read earlier; and
* somebody who reconnected a *different* Atlassian account went on posting under the old
  one — a comment carrying one person's name, sent with another person's token.

The second is the worse of the two, because it looks fine. The comment is well-formed,
the footer names somebody real, and the only way to notice is to check which account
actually posted it.

`gate.accept_saved` existed for exactly this and was only ever reached through the
loopback listener, which the shared server does not use. The fix is one callback:
`build_http_app` hands the connect page `sessions.forget`, and the page calls it after a
save and after a disconnect. The dependency is inverted — a factory — so neither module
has to import the other.

Both failures are now tests, and both were confirmed to bite by making `PerPerson.forget`
a no-op and watching them fail.

## Also worth keeping

The first version of the attribution test queued Bitbucket's responses in order, and the
second person's review consumed one fewer than the first — because the **diff cache is
shared**, so their review did not re-fetch the diff. The cache was doing its job and the
test was wrong. It routes by request now, which is both more honest and lets the answer
depend on which credential asked.

# 16 — Operating a thing that holds credentials

**Status:** done

- [x] One command reports TLS, store reachable, key present, allowlist loaded, and how
      many credentials are live — exiting with a meaningful shell status.
      `--health`: `0` healthy, `1` something to look at, `2` this will not start
- [x] Logs never contain a Bitbucket token, a client credential, an authorization code,
      a session cookie, the vault key, or a pull request body — asserted by a test that
      goes looking for them
- [x] Logs are structured and safe to ship — `BB_MCP_LOG_JSON=true`
- [x] A backup of the store is useless without the key, and the documentation says how to
      back the key up separately
- [x] Key rotation is possible without re-enrolling anybody — `--rotate-key`
- [x] The deployment document states what one compromise costs, in those words, and names
      branch restrictions as the mechanism that survives it
- [x] The architecture document gained the shared deployment (ticket 11), and ADR-0003
      and ADR-0004 each gained a note saying what the shared server does instead

## The test that earns its keep

`tests/test_logs_are_safe_to_ship.py` runs the *failure* paths — a refused token exchange,
a rejected access token, a credential the form would not accept, a vault row that will not
decrypt — and then searches every log line for each secret the server handles.

Failure paths, because the happy path rarely prints anything and "let me add the response
body to the error so we can debug it" is how a token reaches a log. That is not
hypothetical: adding `response.text` to one warning in `oidc.py` makes the test fail, and
the leaked value is the authorization code.

## What `--health` will not do

It reports; it does not repair. Nothing in it writes to the store, and nothing generates a
key — a generated key is a key nobody backed up, and that failure is silent until the next
restart, when every stored credential is unreadable.

## What is deliberately still missing

`docker-compose.shared.yaml` remains development configuration and says so in its own
first paragraph: an in-memory Keycloak, a bootstrap admin password in the file, and a
realm user password in the file. Making it production-shaped means giving Keycloak a real
database, real secrets and its own backups — and probably federating it to an identity
provider that already exists, which is a decision for whoever deploys this rather than one
to guess at here.

# Deploying the shared server

Read this before running it. It holds several people's Bitbucket credentials, and the
things that go wrong with it are not the things that go wrong with a normal service.

## What one compromise costs

**Every credential this server holds can merge pull requests.** Bitbucket sells no
permission that separates commenting from merging
([ADR-0002](adr/0002-comment-only-blast-radius.md)), so there is no weaker token to ask
people for. On a laptop that is one person's exposure. Here it is everybody's, at once,
from a single compromise of this host.

Whoever takes this host gets the encryption key — the process must hold it — and the
database, which the process must read, and therefore **every enrolled person's write
access to every allowlisted repository**. No arrangement of ciphers changes that, and
[the threat model](threat-model-shared-store.md) says so adversary by adversary rather
than leaving it to be discovered.

**The mechanism that survives it is branch restrictions**, configured on the repositories
themselves and enforced by Bitbucket regardless of what this code does. In Bitbucket:
*Repository settings → Branch restrictions*. Restrict who may merge to your default
branch and require the approvals your team expects. That is the only control here that
still works after this server is owned, which makes it the one worth doing first.

Two other things bound the damage, and both are policy rather than code: **keep the
enrolled set small**, and **keep token expiries short** — a stolen set then stops being
worth having on its own schedule.

## Before it runs

- **TLS.** Anthropic's servers connect to this address over the internet. `--health`
  refuses a non-https public URL anywhere but loopback; terminate TLS in front of it.
- **An allowlist.** As on a laptop, the server will not start without one. An absent list
  is indistinguishable from permission to touch every repository the credentials can
  reach.
- **A vault key, backed up.** See below. This is the failure that loses everything
  quietly.
- **Firewall the MCP endpoint if you can.** Anthropic's requests come from
  `160.79.104.0/21`. The part of this server that holds credentials need not face the open
  internet at all; only the sign-in and connect pages must, because a browser goes there.

## The key, and backups

The key is supplied at startup — `BB_MCP_VAULT_KEY`, or `BB_MCP_VAULT_KEY_FILE` naming a
file. **It must not live where the database's backups reach.** That separation is the only
reason a leaked backup is not a leaked credential set, and it is the whole of what the
encryption buys.

Nothing generates a key for you. A generated key is a key nobody backed up, and a key
nobody backed up is every stored credential lost on the next restart, silently — which is
the worst property a failure can have.

**Make it before the first `up`, at an absolute path, outside the working tree:**

```
sudo mkdir -p /etc/bb-pr-mcp
python3 -c "import os,base64;print(base64.b64encode(os.urandom(32)).decode())" \
  | sudo tee /etc/bb-pr-mcp/vault.key >/dev/null

# The container runs as uid 10001. A key made with sudo is root-owned and mode
# 600, which that user cannot read — give it the file rather than widening it.
sudo chown 10001:10001 /etc/bb-pr-mcp/vault.key
sudo chmod 400 /etc/bb-pr-mcp/vault.key
```

Then point `.env` at it — `BB_MCP_VAULT_KEY_PATH=/etc/bb-pr-mcp/vault.key`.

**The ownership is the step that gets skipped.** `chmod 644` would also make the server
start, and it is the wrong fix: a key every account on the host can read is most of the
way to no key at all. The uid is `reviewer` in the image — `docker run --rm --entrypoint
sh bitbucket-pr-review-mcp:local -c id` says which, if it ever changes.

**Absolute, and not a relative path**, because a relative bind source is resolved against
the project directory: `./.bb-pr-mcp/vault.key` means one file on a laptop and a different
one on a server, and `~/...` is expanded by some Docker versions and not others.

Starting without the file is worth recognising, because the error names neither the key
nor the path you set. Docker creates a missing bind source *as a directory*, so on Linux
the container never starts:

```
error mounting ".../.bb-pr-mcp/vault.key" to rootfs at "/run/secrets/vault.key":
not a directory: Are you trying to mount a directory onto a file (or vice-versa)?
```

That is "there is no key" wearing a mount error. Make the file, delete the directory
Docker left in its place, and start again. Where the mount does succeed — Docker Desktop
on Windows creates the directory and mounts it happily — the server refuses to start
instead, and says so in those words.

So: **back the key up separately, somewhere the store's backups do not go, and test
restoring both together before you need to.** A backup you have not restored is a belief,
not a backup.

Rotating it does not disturb anybody:

```
uv run bb-pr-mcp --rotate-key /path/to/new.key
```

Every stored credential is re-sealed under the new key and nobody re-enrols. Afterwards
the store is readable **only** with the new key, so point `BB_MCP_VAULT_KEY_FILE` at it
and restart before doing anything else. Destroy the old key only once the new one is
backed up and that backup has been checked.

## Running it

```
uv run bb-pr-mcp --health     # is this deployment fit to run?
uv run bb-pr-mcp --who        # who has connected a Bitbucket account
uv run bb-pr-mcp --revoke somebody@example.com
```

`--health` exits `0` healthy, `1` something to look at, `2` this will not start — a shell
status a monitoring system can act on. It reports TLS, the key, the store, the allowlist,
whether the authorization server is reachable, and how many people are connected.

## Logs

Set `BB_MCP_LOG_JSON=true` for one JSON object per line, and ship them wherever you ship
logs. What is *in* them is a security property rather than a tidiness one: a token in a
log line is a token in the aggregator, in its backups, in its index, and in the access of
everybody who can search it — a much larger set than the people allowed near the
credential store.

Logs carry the person, the account they connected, and what failed. They do not carry
Bitbucket tokens, the vault key, the OIDC client secret, session cookies, authorization
codes, or pull request bodies. That is asserted by
`tests/test_logs_are_safe_to_ship.py`, which runs the failure paths and then goes looking.

## Taking somebody off

Three places hold something, and this server owns one of them:

| Where | What | Who removes it |
|---|---|---|
| Here | Their stored Bitbucket credential | `--revoke`, or they can, on the connect page |
| Keycloak | Their ability to sign in at all | An operator, in Keycloak |
| Atlassian | The API token itself, everywhere else it works | They, or an Atlassian admin |

`--revoke` says all three every time it runs. Do all three. An offboarding checklist
ticked after step one leaves somebody with access they are no longer supposed to have.

## Configuring it

Everything that differs between deployments is a variable in `.env`. Copy
`.env.example`, fill it in, and neither `docker-compose.yaml` nor the realm file needs
editing. The ones that matter most:

| Variable | What it decides |
|---|---|
| `BB_MCP_PUBLIC_ORIGIN` | The one origin serving both the MCP endpoint and Keycloak |
| `BB_MCP_PUBLIC_URL` | The audience in every access token, and what is typed into Claude |
| `BB_MCP_OIDC_ISSUER` | The issuer this server will trust tokens from |
| `BB_MCP_OIDC_CLIENT_SECRET` | This server's own secret, as the client behind `/connect` |
| `BB_MCP_CONNECTOR_CLIENT_SECRET` | The secret pasted into Claude's connector settings |
| `BB_MCP_VAULT_KEY_PATH` | Where the encryption key is, on the host |
| `POSTGRES_PASSWORD` | Keycloak's database |
| `KC_START_COMMAND` | `start-dev` for an afternoon, `start` for a deployment |

The origin has to be one string that means the same thing in four places: the token's
`iss`, the discovery document, the browser's address bar, and this server's
configuration. Stating it once is what stops those drifting apart.

### How the realm gets those values

`--import-realm` substitutes `${NAME}` in the realm file from the environment, and
`docker-compose.yaml` passes each one through with the development stack as its default.

**The realm file's placeholders carry no defaults, and must not.** Keycloak resolves
`${NAME:default}` to the default *without ever consulting the environment* — a realm
written that way ignores every variable set for it and reports nothing wrong. The bare
`${NAME}` is the form that reads the environment. This is the opposite of what the
documentation's example suggests, and it was established by importing a realm and
reading it back; `tests/test_realm_configuration.py` pins it.

The consequence is that an unset variable is not a fallback. It imports the literal
`${NAME}` as a client secret, and the symptom is a login that fails at its very last
step.

### Changing one afterwards

The realm is imported **once**. `--import-realm` leaves an existing realm alone, so
editing `.env` later does nothing until the realm is imported again:

```
docker compose --profile shared down
docker volume rm bitbucket-pr-review-mcp_keycloak-db
docker compose --profile shared up -d
```

That discards Keycloak's database — accounts and all — and nothing else. **Do not reach
for `down -v`**: it removes every volume in the file, and one of them is the credential
vault. Named explicitly, the vault survives, and nobody has to re-connect their Bitbucket
account.

## What this document does not cover

Running Keycloak properly. The defaults in `docker-compose.yaml` are **development
configuration**: a bootstrap admin whose password is in the file, a realm carrying a user
whose password is in the realm file, and plain http on loopback. A real deployment
overrides all of those in `.env`, gives Keycloak its own backups, and probably federates
it to the identity provider you already have rather than keeping accounts in it.

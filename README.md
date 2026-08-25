# bitbucket-pr-review-mcp

An MCP server that lets a language model read a Bitbucket Cloud pull request and leave
comments on it — anchored to the lines they are about, plus one summary at the top.

The server supplies material and posts words. It does not form opinions: the calling model
does the reviewing, and nothing here holds a review prompt or a model credential.

- **How it works and why:** [docs/architecture.md](docs/architecture.md)
- **The decisions behind it:** [docs/adr/](docs/adr/)
- **The language it uses:** [CONTEXT.md](CONTEXT.md)

## What it cannot do

It can create and update comments. It cannot approve, decline or merge a pull request,
cannot write to branches or files, and never deletes a comment.

**Bitbucket does not sell a permission that separates commenting from merging.** The
credential you give this server is capable of merging your pull requests; nothing about
tokens or scopes prevents it. What prevents it is that no tool asks for it, that a single
chokepoint in the HTTP client refuses it however the request is constructed, and — the
part that does not depend on this code being right — **branch restrictions you configure
on the repository yourself**. See
[ADR-0002](docs/adr/0002-comment-only-blast-radius.md), and read
[Before you start](#before-you-start).

## Before you start

**Configure branch restrictions on any repository you allowlist.** In Bitbucket:
*Repository settings → Branch restrictions*. Restrict who may merge to your default
branch, and require the approvals your team expects. Bitbucket enforces that regardless of
what this server does, which makes it the only guarantee here that survives a bug in this
repository. It takes a minute and it is the difference between "we believe this code is
careful" and "it does not matter if it isn't".

## Install

Requires [uv](https://docs.astral.sh/uv/) and Python 3.13. The same three commands work on
Windows, macOS and Linux:

```
git clone <this repository>
cd bitbucket-pr-review-mcp
uv sync
```

Then tell it which repositories it may touch:

```
cp config/repositories.yaml.example config/repositories.yaml
```

...and edit it. The file lists `workspace/repo` entries, holds no secrets, and is meant to
be committed:

```yaml
repositories:
  - streamstech/db-explorer
  - streamstech/lent-manager
```

A whole workspace can be written as `jantrik/*`. It is the widest entry here — it covers
repositories created after you write it — so the server says so at every startup:

```yaml
repositories:
  - jantrik/*
```

The server refuses to start without a list. An absent one is indistinguishable from
permission to touch every repository your credential can reach. Patterns narrower than a
whole workspace (`*/db-explorer`, `streamstech/db-*`) are refused for the same reason:
they are guesses about naming, and they take in whatever gets named that way next.

## Connecting your Bitbucket account

Run setup and open the link it prints:

```
uv run bb-pr-mcp --setup
```

It serves a page on your own machine — loopback only, on a random port, single-use link,
gone after five minutes — asking for your **Atlassian account email** (not your Bitbucket
username, and not the name you gave the token) and an API token. It verifies the pair
against Bitbucket and shows your display name back before storing anything, then puts the
credential in your OS keychain and closes itself.

Create the token at
<https://id.atlassian.com/manage-profile/security/api-tokens> with **exactly** these four
scopes:

| Scope | Why |
|---|---|
| `read:user:bitbucket` | So the server knows whose comments are its own — without it, every re-review stacks duplicates |
| `read:repository:bitbucket` | Reading files and commits around the change |
| `read:pullrequest:bitbucket` | Reading the pull request itself — granular scopes do not nest, so the write scope below does not cover this |
| `write:pullrequest:bitbucket` | Posting and updating comments |

Nothing wider. A token that can also write to a repository, administer one or run
pipelines is refused at the form and refused again at startup.

Enter the token's expiry date when you set it up and you will be warned a week before it
lapses instead of hitting a 401 mid-review.

You never have to run `--setup` explicitly: with no credential stored, every tool answers
with the setup URL instead of an error.

Check it whenever you like:

```
uv run bb-pr-mcp --check
```

That validates the allowlist, the credential and its scopes, prints who you are posting
as, and exits with a shell status — `0` fine, `1` no usable credential, `2` a token whose
scopes are wrong.

### Removing it

```
uv run bb-pr-mcp --forget
```

That deletes the credential from this device's keychain and nothing else — the token
still exists at Atlassian until you revoke it there, and the command says so. The next
tool call will hand you a fresh setup link.

There is deliberately **no tool** for this. A tool that deletes the credential is a tool
a pull request description can talk a model into calling, and nothing is gained: whoever
wants it gone is at a terminal already.

### The model never sees the token

The token goes from your browser into the keychain, and from there into an
`Authorization` header. It is never an argument to a tool, never in a tool's answer,
never in an error message, and not in the setup URL — that carries a *different*
single-use token, which grants nothing but the right to fill in one form on this machine.
`tests/test_the_token_never_reaches_the_model.py` goes looking for it in all of those
places.

### If the keychain is unavailable

The credential goes in the OS keychain and nowhere else — never a file. On macOS and
Windows that works out of the box. On Linux you need a Secret Service (gnome-keyring or
KWallet) running and unlocked; if there is none, the server stops rather than falling back
to a file ([ADR-0003](docs/adr/0003-atlassian-api-token-not-oauth.md)).

## Running it

The server speaks MCP over stdio. Point your client at it:

```json
{
  "mcpServers": {
    "bitbucket-pr-review": {
      "command": "uv",
      "args": ["run", "--directory", "/path/to/bitbucket-pr-review-mcp", "bb-pr-mcp"]
    }
  }
}
```

On Windows use the same shape with a Windows path
(`"C:\\path\\to\\bitbucket-pr-review-mcp"`). Nothing else differs between platforms.

For Claude Code:

```
claude mcp add bitbucket-pr-review -- uv run --directory /path/to/bitbucket-pr-review-mcp bb-pr-mcp
```

## Running it in Docker

The image speaks MCP over stdio like everything else, so there is no port and nothing to
`up`. Build it, then run it with `-i` and talk to it.

```
docker build -t bitbucket-pr-review-mcp:local .
```

**A container has no keychain**, and the setup page cannot help: it binds a loopback port
*inside* the container, which your browser cannot reach. So a containerised run is
*given* its credential instead of storing one. That is a real downgrade — an environment
variable is visible to `docker inspect` and to anything that can read the process — and it
is a decision rather than a fallback: nothing degrades into it, both variables must be
set, and startup says so every time. See
[ADR-0007](docs/adr/0007-the-container-is-given-its-credential.md).

Put the credential in a file that is **not** in this repository:

```
BB_MCP_EMAIL=you@yourcompany.com
BB_MCP_API_TOKEN=ATATT...
BB_MCP_TOKEN_EXPIRES_ON=2027-08-24
```

Then check it, and wire it into a client:

```
docker run --rm \
  --env-file /path/to/env.docker \
  -v /path/to/repositories.yaml:/config/repositories.yaml:ro \
  bitbucket-pr-review-mcp:local --check
```

```json
{
  "mcpServers": {
    "bitbucket-pr-review": {
      "command": "docker",
      "args": [
        "run", "--rm", "-i",
        "--env-file", "/path/to/env.docker",
        "-v", "/path/to/repositories.yaml:/config/repositories.yaml:ro",
        "bitbucket-pr-review-mcp:local"
      ]
    }
  }
}
```

`docker-compose.yaml` writes the same flags down once: `docker compose run --rm
bitbucket-pr-review`, reading `.env.docker` (gitignored) from this directory.

A few things worth knowing:

- **On Windows, use a Windows-style path in `-v`** (`d:/path/to/repositories.yaml:/config/...`).
  Under Git Bash, prefix the command with `MSYS_NO_PATHCONV=1` or the path is rewritten.
- **The allowlist is mounted, not baked in.** It names the repositories the server may
  touch; that list belongs to whoever runs the image, not to the image.
- **`--setup` exits 2 in a container**, saying where setup can be run instead. Rotating a
  token means restarting with a new one.
- The container runs as a non-root user, read-only, with every capability dropped.

## Connecting to the shared server

The shared deployment — several people, one server, each with their own Bitbucket
account — runs behind a single origin. Both the MCP endpoint and Keycloak are served from
it, because the issuer is a string that has to mean the same thing in the token, the
discovery document, the browser and the configuration.

**Neither Claude Desktop nor Claude Code reaches the server directly over `localhost`.**
A Claude *custom connector* is fetched by Anthropic's infrastructure rather than by your
machine, so a server on your laptop is unreachable however it is configured. The local
loop goes through [`mcp-remote`](https://www.npmjs.com/package/mcp-remote): a stdio bridge
that runs on your machine, performs the OAuth flow in your browser, and speaks HTTP to the
server. Once the server has a public https address, a custom connector reaches it
directly and the bridge is no longer needed.

### Which client id goes where

The realm holds three OAuth clients because three different things authenticate, and
they are not interchangeable. Using the wrong one fails at the *first* step, with
`Invalid parameter: redirect_uri` on a Keycloak error page — which names the parameter
rather than the client, and is the same message for every cause.

| You are configuring | Client id | Secret |
|---|---|---|
| A custom connector, in Claude's settings | `bitbucket-pr-review` | `BB_MCP_CONNECTOR_CLIENT_SECRET` |
| `mcp-remote`, for Claude Code or Desktop | `bitbucket-pr-review-cli` | none — it is a public client |
| Nothing. This server uses it itself, for `/connect` | `bitbucket-pr-review-web` | `BB_MCP_OIDC_CLIENT_SECRET` |

The split follows where the callback lands. A connector's callback is
`https://claude.ai/api/mcp/auth_callback`, on Anthropic's infrastructure, so that client
is confidential and its secret lives there. The bridge's callback is a loopback port on
somebody's laptop, so that client holds no secret at all — a secret in a config file on a
laptop is not a secret, and PKCE is what protects a loopback flow. Registering the
hosted callback on the public client would hand the confidential flow to a client that
cannot keep anything, so it is not registered, and Keycloak refuses it.

Copy `.env.example` to `.env`, then start it:

```
cp .env.example .env      # fill in the secrets; the defaults are the loopback stack
docker compose --profile shared up -d
```

That brings up Postgres, Keycloak, nginx and the review server. `docker compose --profile
shared ps` should show four healthy containers, and `http://localhost:8080/mcp` should
answer `401` with a `WWW-Authenticate` header naming the `bitbucket:review` scope — an
unauthenticated request being refused is the system working.

### Claude Code

Register the bridge once, for every project, with `-s user`:

```
claude mcp add bitbucket-pr-review -s user -- npx -y mcp-remote http://localhost:8080/mcp 3334 --allow-http --static-oauth-client-info "{\"client_id\":\"bitbucket-pr-review-cli\"}"
```

`-s user` writes it to the top-level `mcpServers` key in `~/.claude.json`, which applies
in every directory. The alternatives are `-s local` (this project only, also in
`~/.claude.json`, under `projects`) and `-s project` (a committed `.mcp.json`). Do not
register it at more than one scope: the configs are separate, user scope wins, and the
one you edit later may not be the one being used.

Check it:

```
claude mcp get bitbucket-pr-review
```

which should report `Scope: User config` and `Status: ✔ Connected`. A session picks up
MCP servers when it starts, so an already-running Claude Code will not see a
newly-registered server until it is restarted.

### Giving somebody an account

The realm ships one user, `dev` / `dev-only-not-for-production`, which is a development
credential and says so. Everybody else needs an account in Keycloak before they can sign
in — that is a separate thing from connecting their Bitbucket account afterwards, which
they do themselves at `/connect`.

**In the admin console.** Open `http://localhost:8080/admin`, sign in as the bootstrap
admin (`KC_BOOTSTRAP_ADMIN_USERNAME` / `KC_BOOTSTRAP_ADMIN_PASSWORD` from your `.env`),
switch the realm picker from *master* to *streamstech*, then *Users → Add user*. Fill in
username, email, **first name and last name**, tick *Email verified*, and create. Then
*Credentials → Set password*, and turn **Temporary off** unless you want them prompted to
change it at first login.

**Or from the command line**, which is easier to repeat:

```
docker exec bitbucket-pr-review-mcp-keycloak-1 /opt/keycloak/bin/kcadm.sh \
  config credentials --server http://localhost:8080 --realm master \
  --user admin --password "$KC_BOOTSTRAP_ADMIN_PASSWORD"

docker exec bitbucket-pr-review-mcp-keycloak-1 /opt/keycloak/bin/kcadm.sh \
  create users -r streamstech \
  -s username=somebody -s email=somebody@example.com -s emailVerified=true \
  -s firstName=Some -s lastName=Body -s enabled=true

docker exec bitbucket-pr-review-mcp-keycloak-1 /opt/keycloak/bin/kcadm.sh \
  set-password -r streamstech --username somebody --new-password 'their-password'
```

Under Git Bash, prefix each of these with `MSYS_NO_PATHCONV=1` or `/opt/keycloak/...` is
rewritten into a Windows path and `docker exec` reports that the file does not exist.

Two things that are easy to get wrong, both of which fail at *login* rather than at
creation, with a message that does not point at the cause:

- **First and last name are required.** Keycloak's user profile treats them as mandatory,
  so an account created without them authenticates with `invalid_grant: Account is not
  fully set up`. Nothing warns you when the account is made.
- **`kcadm.sh set-password` without `-t` is already permanent**; passing `-t` makes it
  temporary and leaves the same required action pending.

No roles or group memberships are needed. A new account gets `default-roles-streamstech`
automatically, and that is enough — this server authorizes on the `bitbucket:review`
scope, which is requested during the OAuth flow and consented to, not granted in advance.

Accounts live in Keycloak's database, which is a volume. They survive restarts, and they
do not survive `docker volume rm bitbucket-pr-review-mcp_keycloak-db`.

### Claude Desktop

Claude Desktop has no `mcp add` command; you edit `claude_desktop_config.json` by hand.
Where it lives depends on how Claude was installed:

| Install | Path |
|---|---|
| Windows | `%APPDATA%\Claude\claude_desktop_config.json` |
| Windows, Microsoft Store | `%LOCALAPPDATA%\Packages\Claude_*\LocalCache\Roaming\Claude\claude_desktop_config.json` |
| macOS | `~/Library/Application Support/Claude/claude_desktop_config.json` |

The Store path is the one that catches people out — an installation from the Store ignores
the `%APPDATA%` file entirely, and editing the wrong one changes nothing with no error.

```json
{
  "mcpServers": {
    "bitbucket-pr-review": {
      "command": "cmd",
      "args": [
        "/c", "npx",
        "-y", "mcp-remote",
        "http://localhost:8080/mcp",
        "3335",
        "--allow-http",
        "--static-oauth-client-info", "{\"client_id\":\"bitbucket-pr-review-cli\"}"
      ]
    }
  }
}
```

`deploy/claude_desktop_config.example.json` holds the same thing. Two details in there are
doing real work:

- **`cmd /c` in front of `npx`, on Windows.** Claude Desktop does not spawn through a
  shell, so a bare `"command": "npx"` resolves to a batch file whose own path contains a
  space, and the whole thing dies with `'C:\Program' is not recognized as an internal or
  external command` in the log below. On macOS, drop `cmd` and `/c` and use `"command":
  "npx"`.
- **Port `3335` rather than `3334`.** That number is the bridge's own loopback port, and
  Claude Code's registration already uses `3334`. Two bridges on one port means whichever
  starts second cannot receive its OAuth callback. The realm registers
  `http://127.0.0.1:*/oauth/callback`, so any free port works.

Restart Claude Desktop — fully, from the tray, since closing the window leaves it running
— and check it came up:

```
tail -f "$LOCALAPPDATA/Packages/Claude_*/LocalCache/Roaming/Claude/logs/mcp-server-bitbucket-pr-review.log"
```

`Proxy established successfully between local STDIO and remote
StreamableHTTPClientTransport` is the line that means the bridge is talking to the server.
`Server transport closed unexpectedly` means it exited instead — the reason is a few lines
above it.

The first tool call then opens a Keycloak login (`dev` / `dev-only-not-for-production` in
the development realm, or an account you made above). After signing in, a tool call
answers with a link to `/connect`, where you connect your Atlassian account — that page
makes the browser sign in too, which is why the link is safe to see in a transcript.

A few things worth knowing:

- **The bridge is a *public* OAuth client, with no secret.** A client secret in a config
  file on a laptop is not a secret; PKCE is what protects a loopback flow.
- **Its callback is `http://127.0.0.1:3334/oauth/callback`** — the IP literal rather than
  `localhost`, and `/oauth/callback` rather than Claude Code's `/callback`. The realm
  registers all of them, because getting it wrong fails at the last step of the flow.
- **`--allow-http` is required** while the server is on plain http. A real deployment is
  https, and this server refuses to describe itself over http anywhere but loopback.
- **The `3334` argument is the bridge's own port**, and the realm registers the callback
  on it. Two Claude clients bridging at once want different ports.
- **The `shared` profile's *defaults* are development configuration** — a bootstrap admin
  whose password is in `docker-compose.yaml`, a realm carrying a user whose password is in
  the realm file, and plain http on loopback. Every one of those is a variable with a
  default, so a real deployment overrides them in `.env` rather than editing either file.
  See [docs/deploying-the-shared-server.md](docs/deploying-the-shared-server.md).
- **Changing the origin needs the realm imported again.** The realm is imported once, into
  Keycloak's database; `--import-realm` leaves an existing realm alone. Drop that volume by
  name — `docker volume rm bitbucket-pr-review-mcp_keycloak-db` — and never `down -v`,
  which would take the credential vault with it.

### Operating it

```
uv run bb-pr-mcp --health
uv run bb-pr-mcp --rotate-key /path/to/new.key
```

`--health` says whether the deployment is fit to run — TLS, the vault key, the store, the
allowlist, whether the authorization server is reachable, and how many people are
connected — and exits `0` healthy, `1` something to look at, `2` this will not start.

`--rotate-key` re-seals every stored credential under a new key without anybody
re-enrolling, then tells you the order to do the rest in.

**Read [docs/deploying-the-shared-server.md](docs/deploying-the-shared-server.md) before
running this anywhere real.** It states what one compromise of the host costs, which is
larger than it looks, and what to do about it.

### Who is connected, and taking somebody off

```
uv run bb-pr-mcp --who
uv run bb-pr-mcp --revoke alice@streamstech.com
```

`--who` lists everybody who has connected a Bitbucket account: the opaque id, the
Atlassian email, when they connected, and when their token expires. It decrypts the vault
to answer, and then prints everything except the one field worth decrypting for.

`--revoke` deletes one person's stored credential. It takes an email or enough of the
opaque id to be unambiguous, and refuses rather than guessing when a name matches two
people. **Revoking takes effect on the next tool call**, including on a server that is
already running — the shared server reads the credential through rather than holding it,
precisely so that an operator in another terminal is not waiting for a restart.

What it does *not* do is the part worth reading. Three places hold something after
somebody leaves, and this command owns one of them:

- **Here.** The stored credential is gone.
- **Keycloak.** They can still sign in and connect a new token. Disable their account
  there to stop that.
- **Atlassian.** Their API token still exists and still works everywhere else. Only they,
  or an Atlassian admin, can revoke it.

The command says all three every time, because an offboarding checklist that gets ticked
after step one is worse than no checklist.

Neither is a tool, and that is deliberate: a pull request description must not be able to
talk a Caller into disconnecting a colleague.

## The tools

| Tool | What it does |
|---|---|
| `bitbucket_get_pull_request` | Title, state, author, branches, and the Review Basis |
| `bitbucket_get_pull_request_changes` | Every changed file with counts, flagging binary, generated and lockfile entries |
| `bitbucket_get_pull_request_diff` | The whole diff, or one file — with an anchor gutter showing each line's number |
| `bitbucket_get_pr_comments` | The existing conversation, with anchors, stale flags, and which comments are the server's own |
| `bitbucket_add_pr_comment` | Post one finding or a whole review; validated entirely before any of it is sent |
| `bitbucket_update_pr_comment` | Post or update the one summary comment |
| `bitbucket_get_repository` | Metadata and the default branch |
| `bitbucket_get_file` | Any file in an allowlisted repository, at a ref |
| `bitbucket_get_directory` | A directory listing, at a ref |
| `bitbucket_get_commits` | History for a ref or a pull request |
| `bitbucket_search_code` | Code search inside one allowlisted repository |

A pull request is named by one string: either a Bitbucket URL or the shorthand
`workspace/repo/id`.

## Configuration

Everything below has a working default. Set them in the environment or a `.env` file, all
prefixed `BB_MCP_`:

| Setting | Default | What it does |
|---|---|---|
| `BB_MCP_REPOSITORIES_FILE` | `config/repositories.yaml` | Where the allowlist lives |
| `BB_MCP_LOG_LEVEL` | `INFO` | Logs go to stderr, never stdout |
| `BB_MCP_LOG_JSON` | `false` | One JSON object per line, for shipping to an aggregator |
| `BB_MCP_REQUEST_TIMEOUT_SECONDS` | `30` | Per-request timeout |
| `BB_MCP_MAX_DIFF_CHARACTERS` | `60000` | Diff response ceiling |
| `BB_MCP_MAX_FILE_CHARACTERS` | `40000` | File response ceiling |
| `BB_MCP_MAX_CHANGED_FILES` | `300` | Manifest rows |
| `BB_MCP_MAX_DIRECTORY_ENTRIES` | `200` | Directory rows |
| `BB_MCP_MAX_COMMITS` | `50` | Commit rows |
| `BB_MCP_MAX_SEARCH_RESULTS` | `25` | Search matches |
| `BB_MCP_MAX_COMMENTS` | `200` | Comments read per pull request |

Two more exist and are empty until the shared deployment is finished — the per-device
install described above needs neither, because stdio has exactly one caller:

| Setting | What it does |
|---|---|
| `BB_MCP_PUBLIC_URL` | The address Claude connects to, exactly as it is typed into the connector. It is what tokens must name as their audience |
| `BB_MCP_OIDC_ISSUER` | The Keycloak realm that issues those tokens. It must match the issuer in the realm's discovery document exactly — a trailing slash is a difference |
| `BB_MCP_VAULT_FILE` | Where the per-person credentials are kept. Encrypted under `BB_MCP_VAULT_KEY`, which is not a setting because it must not live in a `.env` beside the data |
| `BB_MCP_OIDC_CLIENT_ID` | The Keycloak client this server signs people in with, so the page that collects an API token can ask who they are |
| `BB_MCP_OIDC_CLIENT_SECRET` | That client's secret. Required for the connect page; without it a caller with no credential is told setup is unavailable rather than sent somewhere useless |

Every ceiling is stated in the response when it bites. Truncation is never silent: a
Caller that cannot tell a truncated diff from a complete one will review the missing half
by assuming it was fine.

## Development

```
uv run pytest                    # the suite
uv run pytest --cov=src          # with coverage
uv run ruff check src tests      # lint
uv run ruff format src tests     # format
```

Tests never touch the network. The seam is the HTTP transport and nothing else, so the
guard, the allowlist and every response reader are exercised as production code.
`tests/recorded/` holds responses captured from a real pull request — see
[docs/architecture.md](docs/architecture.md#tests) for why that matters more than it
sounds.

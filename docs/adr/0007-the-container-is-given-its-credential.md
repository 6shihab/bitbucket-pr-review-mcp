# In a container, the credential is supplied rather than stored

ADR-0003 says the credential lives in the OS keychain and this server stops rather than
putting it anywhere else. A container has no keychain, and the setup page cannot bridge
the gap: it binds a random loopback port *inside* the container, which a browser on the
host cannot reach, and even if it could there would be nowhere durable to save what was
entered.

So a containerised run takes its credential from the environment — `BB_MCP_EMAIL` and
`BB_MCP_API_TOKEN` — and that is worse than a keychain. An environment variable is
readable by anything that can read the process: `docker inspect`, a process listing, an
orchestrator's own logs. We are not going to pretend otherwise.

What makes this acceptable is that it is a *decision* rather than a degradation. The
distinction ADR-0003 actually draws is between a store the Reviewer chose and a fallback
the server took on its own, and the second is what produces secrets in synced dotfiles.
Concretely:

* **Nothing falls back.** There is no path where a failed keychain read reaches for the
  environment. Both variables must be set, which does not happen by accident, and if
  they are not set the server refuses with a message naming this ADR.
* **It is loud.** Startup logs what was traded and why, every run, at WARNING.
* **It is read-only.** `EnvironmentStore.save` raises. The setup listener is replaced by
  one that refuses to open, so nothing can write a credential into a process with
  nowhere to put it, and `--setup` exits 2 explaining where setup *can* be run.

## Consequences

Rotating a token in a container means restarting it with a new one; there is no in-place
setup. That is the right shape anyway — a container's configuration belongs to whoever
starts it, not to the process.

The image cannot be a place credentials accumulate: it has no writable state, runs as a
non-root user, drops every capability, and mounts its allowlist read-only. The one thing
it carries is the code.

On a workstation, use the keychain. This mode exists because there is no keychain to use,
and it is deliberately shaped to look like the exception it is.

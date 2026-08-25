# 12 — HTTP transport, with sessions

**What to build:** The same eleven tools, over streamable HTTP, with a session bound to
the authenticated person — and stdio still working exactly as it does today.

**Status:** done

Two deployment shapes, one review engine. The per-device install remains the right answer
for one person and did not regress: `build_server` still takes a lone `CredentialGate`,
wraps it in `SoleCaller`, and every one of the tests written before this ticket passes
unchanged.

The work was not in the tools. It was in the two things that used to be process-wide, and
in proving a request for one person can never be served with another's.

- [x] Every tool behaves identically over HTTP and over stdio — 11 tools listed over the
      wire, and the same `build_server` builds both
- [x] A session resolves to exactly one person, for its whole life
- [x] `CredentialGate` and `KnownIdentity` are per person; nothing process-wide holds
      either — `sessions.py`, and `server.py` now asks for them per call
- [x] Concurrent sessions for different people cannot see each other's credential,
      identity or in-flight state — `TestWhoseCallItIs` drives two people through one app
      and checks which credential reached Bitbucket
- [x] The diff cache is shared, and the reason that is safe is argued where it is built
- [ ] TLS is required: the server refuses to start without it — **partly.**
      `ProtectedResource` refuses a non-https public URL except on loopback, so a
      deployment cannot describe itself over plain http. Terminating TLS is the proxy's
      job and arrives with it
- [x] stdio needs no authentication and no TLS, and that difference is deliberate

**Two decisions worth keeping:**

**Stateless HTTP.** Streamable HTTP can keep a long-lived session task and feed later
requests into it — and then the work happens in the session's context rather than the
request's, so "whose call is this?" is answered by whoever opened the session. Stateless
mode handles each request on its own, which makes the person bound at the door the person
the tool runs as. It also removes session affinity, which is the difference between
running one of these and running two.

**Pure ASGI middleware.** Starlette's `BaseHTTPMiddleware` consumes the request body
before the app sees it. This project already learned that on the setup listener; the
token check reads headers and nothing else.

**Verified against the real thing, not only in tests:** `bb-pr-mcp --http` running against
the Keycloak in `docker-compose.shared.yaml` serves the metadata document, answers an
unauthenticated call with a 401 carrying `resource_metadata`, accepts a genuine Keycloak
token, and lists all eleven tools. A tool call then says the caller has no Bitbucket
credential yet — which is ticket 13, and is the next thing.

**Not built here, on purpose:** the reverse proxy. Claude needs the MCP endpoint and the
authorization server under one hostname, and that is a deployment concern with two
upstreams; it belongs with ticket 16 or with whatever first needs a public address.

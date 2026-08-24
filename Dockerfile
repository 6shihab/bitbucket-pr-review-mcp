# An MCP server that speaks over stdio, so this image is built to be run with `-i` and
# talked to, not to be a long-lived service. There is no port, no daemon, no entrypoint
# script: the container is one process reading stdin and writing stdout.
#
# Two things this image cannot do, both deliberate (ADR-0007):
#   * store a credential — there is no keychain here, so it is supplied at run time;
#   * open the setup page — it would bind a loopback port inside this container, which
#     the browser on your machine cannot reach.

FROM ghcr.io/astral-sh/uv:0.9-python3.13-bookworm-slim AS build

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app

# Dependencies first, from the lockfile alone: this layer is rebuilt only when the lock
# changes, not on every edit to the source.
COPY pyproject.toml uv.lock README.md ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project --no-dev

COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev


FROM python:3.13-slim-bookworm AS runtime

# Nothing in this server needs root, and nothing in it writes to its own filesystem.
RUN useradd --create-home --uid 10001 reviewer

WORKDIR /app
COPY --from=build --chown=reviewer:reviewer /app/.venv /app/.venv
COPY --from=build --chown=reviewer:reviewer /app/src /app/src

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    BB_MCP_REPOSITORIES_FILE=/config/repositories.yaml

USER reviewer

# The allowlist is mounted, not baked in: it names the repositories this server may
# touch, and that list belongs to whoever runs it rather than to the image.
VOLUME ["/config"]

# `--check` is the honest health check for a stdio process: it proves the allowlist
# parses and the credential works, and exits with a status. It is not a liveness probe —
# there is nothing to keep alive.
HEALTHCHECK --interval=30s --timeout=20s --start-period=5s --retries=2 \
    CMD ["bb-pr-mcp", "--check"]

ENTRYPOINT ["bb-pr-mcp"]

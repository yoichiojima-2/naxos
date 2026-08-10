FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl git jq ripgrep nodejs npm \
    && rm -rf /var/lib/apt/lists/*
RUN npm install -g @anthropic-ai/claude-code

WORKDIR /app
COPY shared /app/shared
COPY sandbox-runner/pyproject.toml sandbox-runner/uv.lock /app/sandbox-runner/
WORKDIR /app/sandbox-runner
RUN uv sync --frozen --no-dev --no-install-project

COPY sandbox-runner/src /app/sandbox-runner/src
RUN uv sync --frozen --no-dev

RUN useradd --create-home --uid 1001 sandbox && mkdir -p /workspace && chown sandbox /workspace
USER sandbox
ENV PATH="/app/sandbox-runner/.venv/bin:$PATH" \
    NAXOS_WORKDIR=/workspace \
    CLAUDE_CONFIG_DIR=/workspace/.claude
ENTRYPOINT ["naxos-sandbox"]

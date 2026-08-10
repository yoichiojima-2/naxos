FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim

WORKDIR /app/egress-proxy
COPY egress-proxy/pyproject.toml egress-proxy/uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY egress-proxy/src ./src
RUN uv sync --frozen --no-dev

ENV PATH="/app/egress-proxy/.venv/bin:$PATH" PORT=8080
ENTRYPOINT ["python", "-m", "naxos_egress.main"]

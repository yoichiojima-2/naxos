FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim

WORKDIR /app
COPY shared /app/shared
COPY control-plane/pyproject.toml control-plane/uv.lock /app/control-plane/
WORKDIR /app/control-plane
RUN uv sync --frozen --no-dev --no-install-project

COPY control-plane/src /app/control-plane/src
COPY control-plane/migrations /app/control-plane/migrations
RUN uv sync --frozen --no-dev

ENV PATH="/app/control-plane/.venv/bin:$PATH" PORT=8080
ENTRYPOINT ["python", "-m", "naxos_cp.main"]
CMD ["api"]

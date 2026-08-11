FROM node:22-slim AS ui
WORKDIR /ui
COPY ui/package.json ui/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY ui .
RUN npm run build

FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim

WORKDIR /app
COPY shared /app/shared
COPY control-plane/pyproject.toml control-plane/uv.lock /app/control-plane/
WORKDIR /app/control-plane
RUN uv sync --frozen --no-dev --no-install-project

COPY control-plane/src /app/control-plane/src
COPY control-plane/migrations /app/control-plane/migrations
COPY docs/skills /app/docs/skills
RUN uv sync --frozen --no-dev
COPY --from=ui /ui/out /app/ui

ENV PATH="/app/control-plane/.venv/bin:$PATH" PORT=8080
ENTRYPOINT ["python", "-m", "naxos_cp.main"]
CMD ["api"]

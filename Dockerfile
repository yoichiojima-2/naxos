FROM node:24-slim AS frontend

WORKDIR /build

COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend ./
RUN npm run build

FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim

WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-default-groups --no-install-project

COPY src ./src
COPY roles.json ./
COPY --from=frontend /build/out ./frontend/out
RUN uv sync --frozen --no-default-groups
RUN useradd -m app && chown -R app:app /app
USER app

ENTRYPOINT ["/app/.venv/bin/python", "-m", "naxos.cli"]

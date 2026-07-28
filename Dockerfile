FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim

WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

COPY src ./src
COPY roles.json ./
RUN useradd -m app && chown -R app:app /app
USER app

ENTRYPOINT ["/app/.venv/bin/python", "-m", "src.main"]

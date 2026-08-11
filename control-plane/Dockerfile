FROM node:22-slim AS ui
WORKDIR /ui
COPY ui/package.json ui/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY ui .
RUN npm run build

# Anthropic's official skills, pinned; seeded at startup alongside docs/skills.
# canvas-design and web-artifacts-builder are excluded: their core assets
# (fonts, tar.gz) are binary and the skill store holds text only.
FROM debian:bookworm-slim AS anthropic-skills
ARG ANTHROPIC_SKILLS_REF=f17010c9bb483898c1d9c9f42dde2b3a98889434
ADD https://github.com/anthropics/skills/archive/${ANTHROPIC_SKILLS_REF}.tar.gz /tmp/skills.tar.gz
RUN mkdir /skills && tar -xzf /tmp/skills.tar.gz -C /skills --strip-components=2 \
    --exclude='*/skills/canvas-design' --exclude='*/skills/web-artifacts-builder' \
    "skills-${ANTHROPIC_SKILLS_REF}/skills"

FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim

WORKDIR /app
COPY shared /app/shared
COPY control-plane/pyproject.toml control-plane/uv.lock /app/control-plane/
WORKDIR /app/control-plane
RUN uv sync --frozen --no-dev --no-install-project

COPY control-plane/src /app/control-plane/src
COPY control-plane/migrations /app/control-plane/migrations
COPY docs/skills /app/docs/skills
COPY --from=anthropic-skills /skills /app/docs/skills
RUN uv sync --frozen --no-dev
COPY --from=ui /ui/out /app/ui

ENV PATH="/app/control-plane/.venv/bin:$PATH" PORT=8080
ENTRYPOINT ["python", "-m", "naxos_cp.main"]
CMD ["api"]

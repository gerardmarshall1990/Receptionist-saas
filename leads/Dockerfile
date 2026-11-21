# Frontdesk leads engine: one image for migrations, the explorer and the crawl loop.
FROM python:3.12-slim

# psql/pg_isready for scripts/scale_loop.sh; ca-certificates for HTTPS crawling.
RUN apt-get update \
 && apt-get install -y --no-install-recommends postgresql-client ca-certificates \
 && rm -rf /var/lib/apt/lists/*
COPY --from=ghcr.io/astral-sh/uv:0.9.0 /uv /usr/local/bin/uv

WORKDIR /app
ENV UV_LINK_MODE=copy UV_PROJECT_ENVIRONMENT=/app/.venv PATH="/app/.venv/bin:$PATH"
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY . .
RUN uv sync --frozen --no-dev

# Runs as an unprivileged user; data lives on a mounted volume.
RUN useradd --create-home leads && mkdir -p /app/data && chown -R leads /app/data
USER leads
ENV LEADS_DATA_DIR=/app/data LEADS_CONFIG_DIR=/app/config LEADS_LOG_FORMAT=json LEADS_CMD=leads
ENTRYPOINT ["leads"]
CMD ["--help"]

# Build context: this standalone data_collection directory.
FROM python:3.13-slim-bookworm AS build

COPY --from=ghcr.io/astral-sh/uv:0.8.14 /uv /usr/local/bin/uv
ENV UV_LINK_MODE=copy
WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
RUN uv sync --frozen --no-dev --no-editable

# Release validation target; test dependencies never enter the runtime.
FROM build AS test
COPY tests ./tests
COPY Dockerfile Jenkinsfile Jenkinsfile.backfill Jenkinsfile.theta ./
RUN uv sync --frozen --no-editable \
    && uv run --frozen pytest -q

FROM python:3.13-slim-bookworm AS runtime
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    XDG_CACHE_HOME=/data/cache \
    PATH="/app/.venv/bin:${PATH}"
COPY --from=build /app/.venv /app/.venv
RUN useradd --create-home --uid 10001 collector \
    && mkdir -p /data \
    && chown collector:collector /data
WORKDIR /data
USER collector
STOPSIGNAL SIGINT
ENTRYPOINT ["collect-stocks"]
CMD ["--help"]

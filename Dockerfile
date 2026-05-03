# syntax=docker/dockerfile:1.7

FROM python:3.13-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    UV_NO_CACHE=1 \
    PATH="/root/.local/bin:${PATH}"

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       libpq5 \
       poppler-utils \
       curl \
       ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && curl -LsSf https://astral.sh/uv/install.sh | sh

COPY docker/run-ats-commands.sh /usr/local/bin/run-ats-commands.sh
COPY docker/run-ats-ratings.sh /usr/local/bin/run-ats-ratings.sh
COPY docker/cron-entrypoint.sh /usr/local/bin/cron-entrypoint.sh

RUN chmod +x \
    /usr/local/bin/run-ats-commands.sh \
    /usr/local/bin/run-ats-ratings.sh \
    /usr/local/bin/cron-entrypoint.sh

RUN uv pip install --system /tmp/dist/*.whl \
    && rm -rf /tmp/dist

CMD ["ats", "--help"]
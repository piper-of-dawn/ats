FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TZ=UTC

WORKDIR /app

COPY dist/*.whl /tmp/ats.whl
COPY docker/cron-entrypoint.sh /usr/local/bin/cron-entrypoint.sh

RUN apt-get update \
    && apt-get install --yes --no-install-recommends cron libpq5 \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir /tmp/ats.whl \
    && chmod +x /usr/local/bin/cron-entrypoint.sh

ENTRYPOINT ["/usr/local/bin/cron-entrypoint.sh"]

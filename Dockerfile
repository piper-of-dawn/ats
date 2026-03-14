FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TZ=UTC

WORKDIR /app

COPY dist/*.whl /tmp/dist/
COPY docker/run-ats-commands.sh /usr/local/bin/run-ats-commands.sh
COPY docker/cron-entrypoint.sh /usr/local/bin/cron-entrypoint.sh

RUN apt-get update \
    && apt-get install --yes --no-install-recommends cron libpq5 \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir /tmp/dist/*.whl \
    && chmod +x /usr/local/bin/run-ats-commands.sh \
    && chmod +x /usr/local/bin/cron-entrypoint.sh

ENTRYPOINT ["/usr/local/bin/cron-entrypoint.sh"]

FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PATH="/root/.local/bin:${PATH}"

RUN apt-get update \
    && apt-get install -y --no-install-recommends software-properties-common ca-certificates curl \
    && add-apt-repository -y ppa:deadsnakes/ppa \
    && apt-get update \
    && apt-get install -y --no-install-recommends python3.13 python3.13-venv \
    && curl -LsSf https://astral.sh/uv/install.sh | sh \
    && apt-get purge -y software-properties-common \
    && apt-get autoremove -y \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY dist/*.whl /tmp/dist/
COPY docker/run-ats-commands.sh /usr/local/bin/run-ats-commands.sh
COPY docker/run-ats-ratings.sh /usr/local/bin/run-ats-ratings.sh
COPY docker/cron-entrypoint.sh /usr/local/bin/cron-entrypoint.sh

RUN apt-get update \
    && apt-get install --yes --no-install-recommends cron libpq5 poppler-utils \
    && rm -rf /var/lib/apt/lists/* \
    && uv pip install --system --python /usr/bin/python3.13 --no-cache /tmp/dist/*.whl \
    && chmod +x /usr/local/bin/run-ats-commands.sh \
    && chmod +x /usr/local/bin/run-ats-ratings.sh \
    && chmod +x /usr/local/bin/cron-entrypoint.sh

CMD ["ats", "--help"]

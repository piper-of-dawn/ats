FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends software-properties-common uv ca-certificates curl \
    && add-apt-repository -y ppa:deadsnakes/ppa \
    && apt-get update \
    && apt-get install -y --no-install-recommends python3.13 python3.13-venv python3.13-distutils \
    && curl -fsS https://bootstrap.pypa.io/get-pip.py -o /tmp/get-pip.py \
    && python3.13 /tmp/get-pip.py \
    && rm -f /tmp/get-pip.py \
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
    && uv pip --system install --no-cache-dir /tmp/dist/*.whl \
    && chmod +x /usr/local/bin/run-ats-commands.sh \
    && chmod +x /usr/local/bin/run-ats-ratings.sh \
    && chmod +x /usr/local/bin/cron-entrypoint.sh

ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]
CMD ["ats", "--help"]

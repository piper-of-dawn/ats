FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY dist/*.whl /tmp/ats.whl

RUN pip install --no-cache-dir /tmp/ats.whl

ENTRYPOINT ["ats"]

FROM python:3.14-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt \
    && useradd --create-home --uid 10001 appuser \
    && mkdir -p /data \
    && chown appuser:appuser /data

COPY Operations/ ./Operations/
COPY MCP/ ./MCP/
COPY frontend/ ./frontend/

# Named volumes can be mounted with root-owned contents even when the image's
# /data directory is owned by appuser. Repair the mount before dropping
# privileges so SQLite can create/update the database and its sidecar files.
RUN printf '%s\n' \
    '#!/bin/sh' \
    'set -eu' \
    'chown appuser:appuser /data' \
    'find /data -maxdepth 1 -type f -exec chown appuser:appuser {} \;' \
    'exec su -s /bin/sh appuser -c "exec uvicorn main:app --host 0.0.0.0 --port 8090 --workers ${OPERATIONS_WORKERS:-2} --limit-concurrency 16 --limit-max-requests 1000"' \
    > /usr/local/bin/operations-entrypoint
RUN chmod 755 /usr/local/bin/operations-entrypoint

WORKDIR /app/Operations

EXPOSE 8090

ENTRYPOINT ["/usr/local/bin/operations-entrypoint"]

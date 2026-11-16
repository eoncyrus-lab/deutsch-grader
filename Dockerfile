FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DG_ENV=dev \
    DG_AUTO_BOOTSTRAP=1 \
    DG_DB_PATH=/data/app.db \
    DG_STORAGE_DIR=/data/objects

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md ./
COPY app ./app
COPY configs ./configs
COPY demo ./demo
COPY eval ./eval
COPY scripts/entrypoint.sh ./scripts/entrypoint.sh

RUN pip install --no-cache-dir . \
    && chmod +x ./scripts/entrypoint.sh \
    && useradd --create-home --uid 10001 app \
    && mkdir -p /data/objects \
    && chown -R app:app /app /data

USER app
EXPOSE 8000

HEALTHCHECK --interval=20s --timeout=5s --start-period=20s --retries=5 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=3)"

CMD ["./scripts/entrypoint.sh"]

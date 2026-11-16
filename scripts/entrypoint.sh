#!/bin/sh
set -eu

set +u
BOOTSTRAP="$DG_AUTO_BOOTSTRAP"
set -u
if [ -z "$BOOTSTRAP" ]; then
  BOOTSTRAP=1
fi

mkdir -p "$(dirname "$DG_DB_PATH")" "$DG_STORAGE_DIR"

if [ "$BOOTSTRAP" = "1" ]; then
  python -m app.bootstrap
fi

python -m app.queue --worker-id "worker-$$" &
WORKER_PID=$!

uvicorn app.main:app --host 0.0.0.0 --port 8000 &
WEB_PID=$!

cleanup() {
  kill "$WEB_PID" "$WORKER_PID" 2>/dev/null || true
}
trap cleanup TERM INT

wait "$WEB_PID"

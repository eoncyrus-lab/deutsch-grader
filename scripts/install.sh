#!/bin/sh
set -eu

ROOT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$ROOT_DIR"

say() { printf '%s\n' "$*"; }

if [ ! -f .env ]; then
  cp .env.example .env
  SECRET=$(python3 -c "import secrets; print(secrets.token_urlsafe(32))" 2>/dev/null || true)
  if [ -n "$SECRET" ]; then
    python3 - <<PY
from pathlib import Path
p = Path(".env")
text = p.read_text()
text = text.replace("DG_SECRET_KEY=change-me-32-bytes-minimum-change-me", "DG_SECRET_KEY=$SECRET")
p.write_text(text)
PY
  fi
  say "Created .env. Add model keys there when you are ready to grade real essays."
fi

if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
  say "Docker found. Building and starting..."
  docker compose up -d --build
  say ""
  say "Waiting for the app to become healthy..."
  i=0
  while [ "$i" -lt 60 ]; do
    if curl -fsS http://127.0.0.1:8000/healthz >/dev/null 2>&1; then
      say "Ready: http://127.0.0.1:8000"
      say "Demo login: teacher@demo.local / Demo2026!"
      exit 0
    fi
    sleep 1
    i=$((i + 1))
  done
  say "Container started but health check is still pending. Run: docker compose logs -f"
  exit 1
fi

say "Docker not found. Falling back to a local Python environment."

if ! command -v uv >/dev/null 2>&1; then
  say "Installing uv..."
  curl -LsSf https://astral.sh/uv/install.sh | sh
  PATH="$HOME/.local/bin:$PATH"
  export PATH
fi

if ! command -v uv >/dev/null 2>&1; then
  say "uv installation failed. Install Python 3.12 and uv, then rerun this script."
  exit 1
fi

uv venv --python 3.12 .venv
uv pip install -e '.[dev]'
.venv/bin/python -m app.bootstrap

mkdir -p data
if [ -f .server.pid ] || [ -f .worker.pid ]; then
  say "Existing pid files found. Stop old processes first."
  exit 1
fi

nohup .venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000 > server.log 2>&1 &
echo $! > .server.pid
nohup .venv/bin/grading-worker > worker.log 2>&1 &
echo $! > .worker.pid

sleep 2
say "Ready: http://127.0.0.1:8000"
say "Demo login: teacher@demo.local / Demo2026!"
say "Logs: server.log and worker.log"

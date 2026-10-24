#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
test -d .venv || python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
(cd frontend && npm ci && npm run build)
exec .venv/bin/python -m uvicorn backend.app.main:app --host 127.0.0.1 --port "${PORT:-8765}"

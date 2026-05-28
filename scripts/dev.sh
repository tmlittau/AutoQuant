#!/usr/bin/env bash
# scripts/dev.sh -- start the dev backend + frontend in parallel.
#
# Backend:  Django on 127.0.0.1:8000  (with --noreload so the process-wide
#           market-data adapter / cache stays stable; restart this script
#           manually to pick up backend code changes)
# Frontend: Vite + Svelte SPA on 127.0.0.1:5173  (proxies /api -> backend)
#
# Press Ctrl+C and both children are killed via the EXIT trap.

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_ROOT"

VENV_PY="$PROJECT_ROOT/.venv/bin/python"
if [[ ! -x "$VENV_PY" ]]; then
  echo "ERROR: venv not found at $VENV_PY -- run scripts/install.sh first." >&2
  exit 1
fi
if [[ ! -d "$PROJECT_ROOT/frontend/node_modules" ]]; then
  echo "ERROR: frontend/node_modules not found -- run scripts/install.sh first." >&2
  exit 1
fi

backend_pid=""
frontend_pid=""
cleanup() {
  printf "\nStopping dev servers...\n"
  [[ -n "$backend_pid" ]]  && kill "$backend_pid"  2>/dev/null || true
  [[ -n "$frontend_pid" ]] && kill "$frontend_pid" 2>/dev/null || true
  wait 2>/dev/null || true
}
trap cleanup INT TERM EXIT

# Run backend.
( cd "$PROJECT_ROOT/backend" \
  && "$VENV_PY" manage.py runserver --noreload 127.0.0.1:8000 ) &
backend_pid=$!

# Run frontend.
( cd "$PROJECT_ROOT/frontend" && npm run dev ) &
frontend_pid=$!

cat <<EOF

============================================================
  Backend:   http://127.0.0.1:8000/api/docs
  Frontend:  http://127.0.0.1:5173/
  Admin:     http://127.0.0.1:8000/admin/
  Press Ctrl+C to stop both.
============================================================

EOF

# Wait until either process exits (or Ctrl+C); cleanup trap handles the rest.
wait

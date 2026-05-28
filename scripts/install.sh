#!/usr/bin/env bash
# scripts/install.sh -- one-shot setup for a fresh checkout.
#
# Creates the Python venv (if missing), installs Python + Node deps, applies
# Django migrations, seeds the DB from the legacy YAML+CSV the first time, and
# refreshes the OpenAPI -> TypeScript types so the SPA compiles.
#
# Re-running is safe and (mostly) idempotent.

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_ROOT"

VENV_DIR="$PROJECT_ROOT/.venv"
VENV_PY="$VENV_DIR/bin/python"

bold() { printf "\033[1m%s\033[0m\n" "$*"; }

# --- 1) Python venv + deps ------------------------------------------------
if [[ ! -x "$VENV_PY" ]]; then
  bold "Creating Python venv at $VENV_DIR..."
  python3 -m venv "$VENV_DIR"
fi

bold "Installing Python deps from requirements.txt..."
"$VENV_PY" -m pip install --quiet --upgrade pip
"$VENV_PY" -m pip install --quiet -r requirements.txt

# --- 2) Frontend deps -----------------------------------------------------
if [[ ! -d "$PROJECT_ROOT/frontend/node_modules" ]]; then
  bold "Installing npm deps..."
  (cd "$PROJECT_ROOT/frontend" && npm install --legacy-peer-deps --silent)
else
  echo "frontend/node_modules already present -- skip npm install."
fi

# --- 3) Django migrations -------------------------------------------------
bold "Applying Django migrations..."
(cd "$PROJECT_ROOT/backend" && "$VENV_PY" manage.py migrate --noinput)

# --- 4) One-time legacy import (only if DB is empty) ----------------------
holdings_count=$(cd "$PROJECT_ROOT/backend" && "$VENV_PY" manage.py shell -c "
from portfolio_app.models import Holding
print(Holding.objects.count())
" 2>/dev/null | tail -1 | tr -dc '0-9')

if [[ "${holdings_count:-0}" -eq 0 ]]; then
  if [[ -f "$PROJECT_ROOT/portfolio.yaml" ]]; then
    bold "DB empty -- importing portfolio.yaml + watchlist.yaml + transactions.csv..."
    (cd "$PROJECT_ROOT/backend" && "$VENV_PY" manage.py import_legacy)
  else
    echo "DB empty and no portfolio.yaml found -- skipping legacy import."
    echo "  After ./scripts/dev.sh, create a superuser and add holdings via the UI:"
    echo "    cd backend && ../.venv/bin/python manage.py createsuperuser"
  fi
else
  echo "DB already has $holdings_count holdings -- skipping legacy import."
fi

# --- 5) Refresh OpenAPI -> TypeScript ------------------------------------
bold "Regenerating OpenAPI types..."
"$PROJECT_ROOT/scripts/regen-types.sh"

# --- 6) Done --------------------------------------------------------------
cat <<'EOF'

============================================================
  Setup complete.

  Start the dev servers (one command):
      ./scripts/dev.sh

  Open:
      Frontend:  http://localhost:5173
      API docs:  http://localhost:8000/api/docs
      Admin:     http://localhost:8000/admin
============================================================
EOF

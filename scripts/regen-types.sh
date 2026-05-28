#!/usr/bin/env bash
# scripts/regen-types.sh -- snapshot the live OpenAPI spec from Django and
# regenerate frontend/src/lib/api-types.ts so the SPA stays in sync with the
# backend schema.
#
# Run this after any backend api.py / schemas.py change.

set -euo pipefail
PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_PY="$PROJECT_ROOT/.venv/bin/python"

if [[ ! -x "$VENV_PY" ]]; then
  echo "ERROR: venv not found at $VENV_PY -- run scripts/install.sh first." >&2
  exit 1
fi

# Dump the OpenAPI spec via the Django test client (no live server needed).
(cd "$PROJECT_ROOT/backend" && "$VENV_PY" manage.py shell <<PY
import json
from django.test import Client
spec = Client().get('/api/openapi.json').json()
out = '$PROJECT_ROOT/frontend/openapi.json'
with open(out, 'w') as fh:
    json.dump(spec, fh, indent=2)
print(f'wrote {out} (paths: {len(spec["paths"])})')
PY
) >/dev/null

# Regenerate TS types.
(cd "$PROJECT_ROOT/frontend" && npm run gen:api --silent) >/dev/null
echo "OpenAPI types refreshed: frontend/src/lib/api-types.ts"

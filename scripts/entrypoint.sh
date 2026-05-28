#!/bin/sh
# Backend container entrypoint:
#   1. apply Django migrations against the mounted SQLite volume
#   2. collect static files (admin CSS, etc.) for WhiteNoise
#   3. first-run only: seed the DB from the legacy YAML+CSV if empty
#   4. exec the CMD (gunicorn by default)

set -e

cd /app/backend

echo "[entrypoint] Running migrations..."
python manage.py migrate --noinput

echo "[entrypoint] Collecting static files..."
python manage.py collectstatic --noinput --clear >/dev/null

HAS_HOLDINGS=$(python manage.py shell -c "from portfolio_app.models import Holding; print(Holding.objects.count())" 2>/dev/null | tr -dc '0-9')
if [ -z "$HAS_HOLDINGS" ] || [ "$HAS_HOLDINGS" = "0" ]; then
    if [ -f /app/portfolio.yaml ]; then
        echo "[entrypoint] Empty DB -- importing legacy portfolio.yaml + watchlist.yaml + data/transactions.csv..."
        python manage.py import_legacy || echo "[entrypoint] (import_legacy failed; continuing)"
    else
        echo "[entrypoint] Empty DB and no /app/portfolio.yaml -- add holdings via the SPA after createsuperuser."
        echo "[entrypoint] (To seed from existing YAML/CSV, see the 'Importing legacy data' section in README.md)"
    fi
fi

exec "$@"

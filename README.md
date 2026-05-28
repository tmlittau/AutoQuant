# AutoQuant

A personal portfolio monitor + quant analytics app. Started as a notebook-driven
Python library (Alpha Vantage / yfinance + pandas + Plotly), now wrapped in a
single-user webapp:

- **Backend:** Django + [django-ninja](https://django-ninja.dev/) (SQLite).
- **Frontend:** Vite + Svelte 5 SPA, Tailwind v4, Plotly.js charts.
- **Analytics core:** the original [`autoquant/`](autoquant/) Python package
  (multi-currency portfolio valuation, technical indicators, BUY/HOLD/TRIM
  signals, Pearson + distance-correlation diversification).

The webapp is the source of truth; the original notebooks in
[`notebooks/`](notebooks/) were prototyping and aren't kept in lock-step.

---

## What's in the app

| View | What it shows |
|---|---|
| **Dashboard** | Cross-sleeve EUR value & P&L, 90-day sparkline, top movers, adapter status |
| **Portfolio › Stocks** | Sunburst (group → holding), group allocation vs. target, value-history stacked area, P&L bar, holdings table, **Add Stock** + **Add Investment** modals |
| **Portfolio › ETFs** | Same chart set, no target-drift (ETFs are diversified by construction) |
| **Diversification** | Pearson + distance correlation heatmaps side-by-side, lookback slider, summary KPI cards |
| **Watchlist** | Scored table + 2-D signal-map scatter, **Add to Watchlist** modal |
| **Single-stock / Explore** | Quote + signal breakdown + price/SMA/Bollinger/RSI/MACD/drawdown for *any* ticker (owned, watched, or discovered via the search) |
| **Transactions** | Filterable ledger, inline edit (note/fee), delete with confirm, CSV export |
| **Settings** | Adapter switcher (yfinance / Alpha Vantage), AV API-key entry, AV quota chip, clear-cache |

A global **search bar** in the top nav hits the live adapter's symbol-search
endpoint (yfinance Search or Alpha Vantage `SYMBOL_SEARCH`) and routes to
`/stock/:ticker` — so you can investigate any public stock, then click **Add to
Portfolio / Watchlist** to bring it in.

---

## Project layout

```
AutoQuant/
├── autoquant/           # Python analytics library (unchanged from the notebooks)
│   ├── adapters/         # market-data adapters (yfinance default, Alpha Vantage optional)
│   ├── portfolio.py      # ledger + valuation + correlation
│   ├── signals.py        # BUY/HOLD/TRIM scoring
│   ├── metrics.py        # SMA/EMA/RSI/MACD/Bollinger/z-score/drawdown
│   └── ...
├── backend/             # Django project
│   ├── manage.py
│   ├── backend/         # settings.py, urls.py, wsgi.py
│   └── portfolio_app/   # models, api.py (ninja), repository, registry, schemas, serialize
├── frontend/            # Vite + Svelte 5 SPA
│   └── src/
│       ├── App.svelte, main.ts, app.css
│       ├── components/  # Layout, GlobalSearch, AdapterChip, Modal, AddInvestmentModal, AddStockModal, PortfolioView, KpiCard
│       ├── lib/         # api.ts, api-types.ts (generated), format.ts, stores.ts, PlotlyChart.svelte
│       └── routes/      # Dashboard, Portfolio, Watchlist, Diversification, Stock, Transactions, Settings, NotFound
├── data/                # transactions.csv (legacy CSV; once-imported into SQLite)
├── notebooks/           # original Jupyter notebooks (kept for reference)
├── portfolio.yaml       # initial portfolio structure (one-time import source)
├── watchlist.yaml       # initial watchlist (one-time import source)
├── scripts/
│   ├── install.sh       # one-shot setup
│   ├── dev.sh           # start both dev servers
│   ├── regen-types.sh   # refresh OpenAPI -> TypeScript after backend changes
│   └── seed_portfolio.py
├── requirements.txt
└── README.md            # this file
```

---

## Prerequisites

- **Python 3.13+** with `python3 -m venv` available
- **Node.js 20+** (Vite 8 + Svelte 5)
- An **`.env`** file in the project root with `AV_API_KEY=...` if you want to
  use the Alpha Vantage adapter. The yfinance adapter (default) needs no key.

---

## Setup (one command)

From the repo root:

```bash
./scripts/install.sh
```

This will:

1. Create a Python venv at `.venv/` (if missing).
2. Install Python deps from `requirements.txt` (Django, django-ninja,
   WhiteNoise, gunicorn, pandas, yfinance, …).
3. Install npm deps in `frontend/`.
4. Apply Django migrations (creates `backend/db.sqlite3`).
5. **First run only, if `portfolio.yaml` exists at the repo root:** import
   `portfolio.yaml` + `watchlist.yaml` + `data/transactions.csv` into the DB.
   On a fresh clone with no YAML files present this step is skipped — create
   a superuser and add holdings via the SPA instead.
6. Regenerate `frontend/src/lib/api-types.ts` from the live OpenAPI spec.

It's safe to re-run — steps 5 + 6 are skipped/idempotent.

---

## Dev (one command)

```bash
./scripts/dev.sh
```

Starts:

- **Backend** on `http://127.0.0.1:8000` (`manage.py runserver --noreload`)
  — `--noreload` keeps the process-wide adapter / yfinance cache stable;
  restart this script manually to pick up backend code changes.
- **Frontend** on `http://127.0.0.1:5173` (`vite`)
  — proxies `/api`, `/admin`, `/static` to the backend.

Open `http://localhost:5173/`. Ctrl+C stops both processes via the script's
cleanup trap.

Useful endpoints during dev:

- `http://localhost:8000/api/docs` — OpenAPI Swagger UI
- `http://localhost:8000/admin/` — Django admin (create a superuser with
  `cd backend && ../.venv/bin/python manage.py createsuperuser`)

### Regenerating typed API client

After any change to `backend/portfolio_app/api.py` or `schemas.py`:

```bash
./scripts/regen-types.sh
```

Snapshots `backend` → `frontend/openapi.json` → `frontend/src/lib/api-types.ts`
via `openapi-typescript`.

---

## Production deployment (TrueNAS + Cloudflare Zero Trust)

```
[ phone / browser ] -- HTTPS -->
        [ Cloudflare edge ]   -- Zero Trust tunnel -->
                [ TrueNAS host : 3000 ]   -- docker compose network -->
                        frontend (nginx) → SPA bundle + reverse-proxy
                        backend  (gunicorn) → Django + autoquant
```

Throughout this section, replace `your-host.example.com` with the actual
Cloudflare-fronted hostname you intend to use (e.g. a subdomain of your own
Cloudflare-managed zone). **Do not** commit your real hostname into the repo
— it belongs in `.env` only.

* Backend container runs on the internal compose network only (port 8000
  **never bound to the host**). The frontend (nginx) container is the only
  ingress; it serves the SPA bundle and reverse-proxies `/api/*`, `/admin/*`
  and `/static/*` to the backend.
* Cloudflare terminates TLS at the edge and the Zero Trust tunnel delivers
  plain HTTP to the TrueNAS host on **port 3000** (configurable via
  `AUTOQUANT_HOST_PORT`). No router ports opened, no certbot needed.

### Deployment files in this repo

| File | Purpose |
|---|---|
| `Dockerfile.backend` | Python 3.13 + autoquant + Gunicorn |
| `Dockerfile.frontend` | Stage 1 builds the SPA with Vite; stage 2 is nginx |
| `frontend/nginx.conf` | Serves the SPA + reverse-proxies dynamic paths to `backend:8000` |
| `docker-compose.yml` | Two services + two named volumes (`autoquant_db`, `autoquant_cache`) |
| `scripts/entrypoint.sh` | Backend container init: migrate → collectstatic → first-run legacy import |
| `.env.example` | Template for required env vars |
| `.dockerignore` | Keeps the build context lean |

### Step-by-step

**1.** Clone the repo to a TrueNAS dataset (e.g. `/mnt/tank/apps/autoquant`).

**2.** Create `.env` from the template:

```bash
cp .env.example .env
```

Edit and set at minimum:

```dotenv
# Required -- no defaults; `docker compose up` will refuse to start otherwise.
DJANGO_SECRET_KEY=<paste a strong random string>
DJANGO_ALLOWED_HOSTS=your-host.example.com,localhost,127.0.0.1
DJANGO_CSRF_TRUSTED_ORIGINS=https://your-host.example.com

# Optional
AUTOQUANT_HOST_PORT=3000      # change if 3000 is taken on TrueNAS
AV_API_KEY=                    # or enter via the UI later
```

Generate the secret with:

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(64))"
```

**3.** Build and start:

```bash
docker compose build
docker compose up -d
```

The backend container's entrypoint will run migrations and `collectstatic`.
The SQLite DB starts **empty** — no portfolio data is baked into the image.

**4.** Initialise the DB (one-time, run once per fresh install):

```bash
# 4a. Create the single superuser account that can log into the SPA.
docker compose exec backend python backend/manage.py createsuperuser
```

That's enough to start using the app — log in via the SPA and add your
holdings through the **Add Stock** / **Add Investment** modals. Skip 4b unless
you already have a populated YAML/CSV dataset to migrate.

**4b. (Optional) Seed from existing YAML + CSV.** If you have a populated
`portfolio.yaml`, `watchlist.yaml`, and `data/transactions.csv` from an
earlier local install, copy them into the running container and run the
import command:

```bash
docker compose cp portfolio.yaml         backend:/app/portfolio.yaml
docker compose cp watchlist.yaml         backend:/app/watchlist.yaml
docker compose cp data/transactions.csv  backend:/app/data/transactions.csv
docker compose exec backend python backend/manage.py import_legacy
```

(`import_legacy --force` overwrites existing rows; without `--force` it
refuses to run if the DB already has data.) After import the seed files can
be deleted from the container — the SQLite volume is the source of truth
from now on.

**5.** In the **Cloudflare Zero Trust dashboard**:
   * Networks → Tunnels → create a tunnel (or reuse an existing one).
   * On the TrueNAS box (or inside the tunnel container), run `cloudflared`
     with the tunnel token.
   * Add a **Public hostname**:
     - Subdomain + domain: your subdomain + your Cloudflare-managed zone
       (e.g. `app` + `your-domain.example.com`)
     - Service type: `HTTP`
     - URL: `<truenas-lan-ip>:3000` (e.g. `192.168.1.42:3000`)

**6.** Open `https://your-host.example.com/` — you'll see the login screen,
sign in with the superuser, you're done.

### Operational notes

* **Backend is not externally reachable.** `docker compose ps` shows
  `frontend` mapping `3000 → 80`; `backend` only has `8000/tcp` exposed
  internally (no host port). The Cloudflare Tunnel only reaches port 3000.
* **Persistent state** lives in two docker volumes:
  - `autoquant_db` (mounted at `/data` in the backend container) — SQLite
    file (`db.sqlite3`).
  - `autoquant_cache` (`/app/.cache`) — Alpha Vantage on-disk response cache
    (only used if you switch adapter to AV).
  Back these up with `docker volume inspect` paths or `tar` from inside the
  container.
* **Updating after a code change:**

  ```bash
  git pull                       # or copy files
  docker compose build
  docker compose up -d
  ```

  Migrations run automatically; the SQLite volume persists.

* **Restoring / migrating between hosts:** copy the two volumes (or just the
  `db.sqlite3` file from `autoquant_db`) to the new TrueNAS box and
  `docker compose up -d` there.

* **Cloudflare Access (optional extra layer).** Add a CF Access application
  on the same hostname for additional edge-side auth (email OTP, hardware
  key, etc.) on top of Django's own session login.

* **Watching logs:**

  ```bash
  docker compose logs -f backend
  docker compose logs -f frontend
  ```

### Why only port 3000 is exposed

The compose file uses `expose: ["8000"]` (not `ports:`) for the backend, so
Docker only opens that port inside the named network. Only the frontend
container — which is on the same network — can reach `http://backend:8000`.
From the TrueNAS host's network stack, port 8000 is invisible.

If you ever need to reach the backend directly (e.g. for debugging) without
poking at `docker compose exec`, you can temporarily add
`ports: ["127.0.0.1:8001:8000"]` to the backend service — but that's
opt-in.

---

## Common tasks

| Task | Command |
|---|---|
| Apply migrations after a model change | `cd backend && ../.venv/bin/python manage.py makemigrations && ../.venv/bin/python manage.py migrate` |
| Create / reset the superuser | `cd backend && ../.venv/bin/python manage.py createsuperuser` |
| Open the Django shell | `cd backend && ../.venv/bin/python manage.py shell` |
| Re-import the legacy YAML/CSV (wipes existing DB rows!) | `cd backend && ../.venv/bin/python manage.py import_legacy --force` |
| Regenerate the typed API client | `./scripts/regen-types.sh` |
| Build the production SPA bundle | `cd frontend && npm run build` |
| Clear the yfinance in-process price cache | from the UI: Settings → Clear caches |
| Switch market-data adapter | from the UI: Settings → choose adapter → Apply |

---

## Adapters

Two market-data adapters share one `MarketDataAdapter` interface:

- **yfinance** (default) — no key, no daily cap, broad coverage incl. European
  listings via Yahoo suffixes (`.L`, `.PA`, `.AS`, `.DE`, …). The adapter
  auto-maps from canonical Alpha-Vantage-style suffixes (`.LON`/`.PAR`/`.AMS`/`.DEX`)
  so the same YAML works with either provider.
- **alphavantage** — needs an API key (free tier is ~25 requests/day, so use
  yfinance for normal browsing; flip to AV from Settings when you need
  something Yahoo doesn't have). Responses are disk-cached at
  `.cache/alphavantage/`.

The active adapter is a process-wide singleton built in
`portfolio_app.apps.PortfolioAppConfig.ready()` and hot-swappable from the
Settings page.

---

## Tech stack

- Python 3.13 · pandas 3 · numpy 2 · yfinance 1.4 · Plotly 6
- Django 5.1+ · django-ninja 1.6+ · WhiteNoise 6+ · Gunicorn 23+
- TypeScript 6 · Vite 8 · Svelte 5 · Tailwind CSS v4
- Plotly.js 3 · openapi-fetch + openapi-typescript (generated typed client)

## Status

All 8 implementation phases are complete:

1. Backend skeleton + DB models + legacy YAML/CSV import
2. Read-only API (portfolio, history, dashboard, diversification, watchlist
   signals, transactions, instruments, settings)
3. Frontend skeleton, Dashboard, global search bar, Plotly wrapper
4. Portfolio Stocks + ETFs sub-tabs (sunburst + allocation + value history +
   P&L bar + holdings table)
5. Add Investment modal + Transactions ledger view
6. Add Stock modal, Watchlist view, full Single-stock TA panel
7. Pearson + distance correlation, SMA 200 + drawdown, Settings page
8. Session auth + CSRF, AuditEntry on every mutation, pytest smoke suite,
   docker-compose with isolated backend, TrueNAS + Cloudflare Tunnel runbook

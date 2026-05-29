"""HTTP API for the AutoQuant webapp (django-ninja).

Read-only endpoints (Phase 2). Mutating endpoints + auth come in later phases.
All endpoints delegate to the existing ``autoquant`` analytics functions via the
repository layer, so there's exactly one place the DB schema is mapped onto
pandas. Responses are validated by Pydantic schemas in :mod:`schemas`; pandas
objects are converted to JSON-safe shapes by :mod:`serialize` first.

The adapter is a process singleton (see :mod:`registry`) -- one yfinance
``_cache`` is reused across requests.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any, Optional

import pandas as pd
from django.contrib.auth import authenticate as django_authenticate
from django.contrib.auth import login as django_login
from django.contrib.auth import logout as django_logout
from django.core.cache import cache
from django.db import transaction as db_transaction
from django.http import HttpResponse
from ninja import File, NinjaAPI, Query
from ninja.errors import HttpError
from ninja.files import UploadedFile
from ninja.security import django_auth

import autoquant as aq
from autoquant import metrics as m
from autoquant import portfolio as pf
from autoquant import signals as sg
from autoquant.adapters.base import DataUnavailableError
from autoquant.client import AlphaVantageError, RateLimitError

from . import repository as repo
from . import serialize as ser
from .audit import audit
from .models import AssetClass, AuditEntry, GroupConfig, Holding, HoldingKind, Transaction
from .registry import get_registry
from .schemas import (
    AdapterStatus,
    DashboardOut,
    DiversificationOut,
    EstimateProceedsOut,
    EstimateSharesOut,
    HoldingPositionOut,
    ErrorOut,
    GroupCreate,
    GroupOut,
    GroupPatch,
    ImportResultOut,
    RowImportError,
    LoginIn,
    MeOut,
    GroupSummary,
    HoldingCreate,
    HoldingOut,
    HistoryFrameOut,
    IndicatorsOut,
    InstrumentQuoteOut,
    InstrumentSearchHit,
    PortfolioPosition,
    PortfolioSignalsOut,
    PortfolioSnapshotOut,
    PortfolioTotals,
    ScoreOut,
    CacheClearedOut,
    SettingsOut,
    SettingsUpdate,
    Sparkline,
    TopMover,
    TransactionCreate,
    TransactionOut,
    TransactionPatch,
    ValueHistoryOut,
    WatchlistOut,
    WatchlistScoreOut,
)

api = NinjaAPI(
    title="AutoQuant API",
    version="0.1.0",
    description=(
        "Backend for the AutoQuant portfolio webapp. Session-auth + CSRF "
        "enforced by django_auth on every endpoint except /auth/login, /me, "
        "and /csrf."
    ),
    auth=django_auth,
)
# ``django_auth`` is the ninja SessionAuth class: it returns 401 if
# ``request.user`` isn't authenticated AND enforces a valid CSRF token on
# unsafe methods. Per-endpoint ``auth=None`` overrides this where needed
# (login + public health/auth-status). CSRF cookie is primed via /api/csrf,
# which is served by a plain Django view in backend/backend/urls.py.


# ============================================================================ #
# Helpers
# ============================================================================ #
def _tx_revision() -> str:
    """Cache-busting key. Includes both max id (changes on insert) and count
    (changes on delete) so the cache busts on either mutation."""
    last = (
        Transaction.objects.order_by("-id").values_list("id", flat=True).first()
    ) or 0
    count = Transaction.objects.count()
    return f"{last}:{count}"


def _slice_range(df: pd.DataFrame, range_str: str) -> pd.DataFrame:
    """Trim a date-indexed frame to a UI-friendly range token."""
    n_map = {"1mo": 21, "3mo": 63, "6mo": 126, "1y": 252, "2y": 504, "5y": 1260}
    n = n_map.get(range_str)
    return df.tail(n) if n is not None else df


def _filter_dates(
    df: pd.DataFrame, from_: Optional[date], to_: Optional[date]
) -> pd.DataFrame:
    if from_ is not None:
        df = df.loc[df.index >= pd.Timestamp(from_)]
    if to_ is not None:
        df = df.loc[df.index <= pd.Timestamp(to_)]
    return df


def _portfolio_dict() -> dict:
    return repo.build_portfolio_dict(kind=HoldingKind.PORTFOLIO)


def _watchlist_dict() -> dict:
    return repo.build_portfolio_dict(kind=HoldingKind.WATCHLIST)


# ============================================================================ #
# Error handlers
# ============================================================================ #
@api.exception_handler(DataUnavailableError)
def _data_unavailable(request, exc):
    return api.create_response(request, {"detail": str(exc)}, status=404)


@api.exception_handler(RateLimitError)
def _rate_limited(request, exc):
    return api.create_response(
        request, {"detail": str(exc), "limited": True}, status=429
    )


@api.exception_handler(AlphaVantageError)
def _av_error(request, exc):
    return api.create_response(request, {"detail": str(exc)}, status=502)


# ============================================================================ #
# Auth (login / logout / current-user). Open endpoints have ``auth=None`` to
# override the API-wide ``django_auth`` default.
# ============================================================================ #
@api.get("/auth/me", response=MeOut, auth=None, tags=["auth"])
def me(request):
    """Cheap probe used by the SPA on boot to decide whether to show the
    login page or the app. Never errors."""
    if request.user.is_authenticated:
        return {"authenticated": True, "username": request.user.username}
    return {"authenticated": False}


@api.post(
    "/auth/login",
    response={200: MeOut, 401: ErrorOut},
    auth=None,
    tags=["auth"],
)
def login_view(request, body: LoginIn):
    """Authenticate against Django's user model + start a session. POST is
    deliberately open (``auth=None``) so the user can log in without already
    having a session; CSRF for login is left to same-origin SOP for the SPA."""
    user = django_authenticate(request, username=body.username, password=body.password)
    if user is None or not user.is_active:
        return 401, {"detail": "invalid credentials"}
    django_login(request, user)
    audit(request, "/auth/login", "POST", {"username": user.username})
    return 200, {"authenticated": True, "username": user.username}


@api.post("/auth/logout", response={200: MeOut}, tags=["auth"])
def logout_view(request):
    """End the current session. Requires being logged in (uses the default
    ``django_auth``) and thus enforces CSRF on the POST."""
    if request.user.is_authenticated:
        audit(request, "/auth/logout", "POST", {"username": request.user.username})
    django_logout(request)
    return 200, {"authenticated": False}


# ============================================================================ #
# Portfolio snapshot
# ============================================================================ #
@api.get("/portfolio", response=PortfolioSnapshotOut, tags=["portfolio"])
def get_portfolio(request, asset_class: str = "stocks"):
    """Current positions for ``asset_class`` valued in EUR, plus group breakdown."""
    if asset_class not in ("stocks", "etfs"):
        raise HttpError(400, "asset_class must be 'stocks' or 'etfs'")

    adapter = get_registry().adapter
    portfolio = _portfolio_dict()
    tx_df = repo.build_transactions_df()

    positions = pf.current_positions(tx_df, portfolio, asset_class=asset_class)

    if positions.empty:
        return {
            "asset_class": asset_class,
            "positions": [],
            "by_group": [],
            "totals": {"value_eur": 0.0, "cost_eur": 0.0, "pnl_eur": 0.0, "return_pct": None},
            "fx_rates": {},
        }

    prices_local = pf.latest_prices_local(adapter, list(positions.index))
    ccy_map = pf.ticker_to_currency(portfolio, asset_class)
    fx_rates = pf.fx_rates_for(adapter, ccy_map.values())
    valued = pf.value_positions(positions, prices_local, ccy_map, fx_rates)
    group_df = pf.group_summary(valued, portfolio)
    totals = pf.portfolio_totals(valued)

    positions_out = []
    for ticker, row in valued.iterrows():
        positions_out.append(
            {
                "ticker": ticker,
                "name": row.get("name", ticker),
                "group": row["group"],
                "currency": row.get("currency", ccy_map.get(ticker, "USD")),
                "shares": ser.safe(row["shares"]) or 0.0,
                "invested_eur": ser.safe(row["invested_eur"]) or 0.0,
                "fees_eur": ser.safe(row.get("fees_eur")) or 0.0,
                "price_local": ser.safe_float(row.get("price_local")),
                "price_eur": ser.safe_float(row.get("price_eur")),
                "value_eur": ser.safe_float(row.get("value_eur")),
                "cost_eur": ser.safe(row["cost_eur"]) or 0.0,
                "pnl_eur": ser.safe_float(row.get("pnl_eur")),
                "return_pct": ser.safe_float(row.get("return_pct")),
                "weight": ser.safe_float(row.get("weight")),
            }
        )

    by_group_out = []
    for group, row in group_df.iterrows():
        by_group_out.append(
            {
                "group": group,
                "value_eur": ser.safe(row["value_eur"]) or 0.0,
                "cost_eur": ser.safe(row["cost_eur"]) or 0.0,
                "pnl_eur": ser.safe(row["pnl_eur"]) or 0.0,
                "return_pct": ser.safe_float(row.get("return_pct")),
                "weight": ser.safe_float(row.get("weight")),
                "target_weight": ser.safe_float(row.get("target_weight")),
                "drift_pct": ser.safe_float(row.get("drift_pct")),
            }
        )

    return {
        "asset_class": asset_class,
        "positions": positions_out,
        "by_group": by_group_out,
        "totals": {
            "value_eur": ser.safe(totals["value_eur"]) or 0.0,
            "cost_eur": ser.safe(totals["cost_eur"]) or 0.0,
            "pnl_eur": ser.safe(totals["pnl_eur"]) or 0.0,
            "return_pct": ser.safe_float(totals.get("return_pct")),
        },
        "fx_rates": {k: float(v) for k, v in fx_rates.items()},
    }


# ============================================================================ #
# Value history
# ============================================================================ #
@api.get("/portfolio/history", response=ValueHistoryOut, tags=["portfolio"])
def get_history(
    request,
    asset_class: str = "stocks",
    from_: Optional[date] = Query(None, alias="from"),
    to_: Optional[date] = Query(None, alias="to"),
):
    """EUR value time-series for ``asset_class`` (by ticker, by group, total, invested)."""
    if asset_class not in ("stocks", "etfs"):
        raise HttpError(400, "asset_class must be 'stocks' or 'etfs'")

    adapter = get_registry().adapter
    portfolio = _portfolio_dict()
    tx_df = repo.build_transactions_df()
    hist = pf.value_history(adapter, tx_df, portfolio, asset_class=asset_class)

    total = _filter_dates(hist["total"].to_frame("total"), from_, to_)["total"]
    by_ticker = _filter_dates(hist["by_ticker"], from_, to_)
    by_group = _filter_dates(hist["by_group"], from_, to_)
    invested = _filter_dates(hist["invested"].to_frame("invested"), from_, to_)["invested"]

    return {
        "asset_class": asset_class,
        "dates": [d.date().isoformat() if hasattr(d, "date") else str(d) for d in total.index],
        "by_ticker": {
            str(col): ser.series_to_list(by_ticker[col]) for col in by_ticker.columns
        },
        "by_group": {
            str(col): ser.series_to_list(by_group[col]) for col in by_group.columns
        },
        "total": ser.series_to_list(total),
        "invested": ser.series_to_list(invested),
    }


# ============================================================================ #
# Dashboard
# ============================================================================ #
@api.get("/dashboard", response=DashboardOut, tags=["dashboard"])
def get_dashboard(request):
    """Cross-sleeve KPIs + 90-day sparkline + top movers + adapter status."""
    reg = get_registry()
    adapter = reg.adapter
    portfolio = _portfolio_dict()
    tx_df = repo.build_transactions_df()

    cache_key = f"dashboard:{reg.name}:{_tx_revision()}"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    # --- per-sleeve totals ---
    def _sleeve_totals(asset_class: str) -> dict:
        positions = pf.current_positions(tx_df, portfolio, asset_class=asset_class)
        if positions.empty:
            return {"value_eur": 0.0, "cost_eur": 0.0, "pnl_eur": 0.0, "return_pct": None}
        prices = pf.latest_prices_local(adapter, list(positions.index))
        ccy = pf.ticker_to_currency(portfolio, asset_class)
        fx = pf.fx_rates_for(adapter, ccy.values())
        valued = pf.value_positions(positions, prices, ccy, fx)
        totals = pf.portfolio_totals(valued)
        return {
            "value_eur": ser.safe(totals["value_eur"]) or 0.0,
            "cost_eur": ser.safe(totals["cost_eur"]) or 0.0,
            "pnl_eur": ser.safe(totals["pnl_eur"]) or 0.0,
            "return_pct": ser.safe_float(totals.get("return_pct")),
        }

    stocks_totals = _sleeve_totals("stocks")
    etfs_totals = _sleeve_totals("etfs")
    combined = {
        "value_eur": stocks_totals["value_eur"] + etfs_totals["value_eur"],
        "cost_eur": stocks_totals["cost_eur"] + etfs_totals["cost_eur"],
        "pnl_eur": stocks_totals["pnl_eur"] + etfs_totals["pnl_eur"],
        "return_pct": None,
    }
    if combined["cost_eur"]:
        combined["return_pct"] = (combined["value_eur"] / combined["cost_eur"] - 1) * 100

    # --- sparkline: last 90 days of total EUR value, both sleeves combined ---
    sleeves_total = []
    for ac in ("stocks", "etfs"):
        if pf.all_tickers(portfolio, ac):
            hist = pf.value_history(adapter, tx_df, portfolio, asset_class=ac)
            sleeves_total.append(hist["total"])
    if sleeves_total:
        combined_total = pd.concat(sleeves_total, axis=1).sum(axis=1).tail(90)
    else:
        combined_total = pd.Series(dtype=float)
    sparkline = {
        "dates": [d.date().isoformat() if hasattr(d, "date") else str(d) for d in combined_total.index],
        "values": ser.series_to_list(combined_total),
    }

    # --- top movers: largest |daily % change| across all portfolio tickers ---
    all_tickers = pf.all_tickers(portfolio, "all")
    movers: list[dict] = []
    if all_tickers:
        closes = adapter.get_close_prices(all_tickers, interval="daily").ffill()
        if len(closes) >= 2:
            chg = (closes.iloc[-1] / closes.iloc[-2] - 1) * 100
            chg = chg.dropna()
            name_map = pf.ticker_to_name(portfolio, "all")
            group_map = pf.ticker_to_group(portfolio, "all")
            asset_class_map = {
                **{t: "stocks" for t in pf.all_tickers(portfolio, "stocks")},
                **{t: "etfs" for t in pf.all_tickers(portfolio, "etfs")},
            }
            for ticker in chg.abs().sort_values(ascending=False).head(5).index:
                movers.append(
                    {
                        "ticker": ticker,
                        "name": name_map.get(ticker, ticker),
                        "asset_class": asset_class_map.get(ticker, "stocks"),
                        "change_pct": float(chg[ticker]),
                        "last_close": float(closes[ticker].iloc[-1]),
                        "value_eur": None,  # filled by frontend if it wants
                    }
                )

    result = {
        "stocks": stocks_totals,
        "etfs": etfs_totals,
        "combined": combined,
        "sparkline": sparkline,
        "top_movers": movers,
        "adapter": {"name": reg.name, "av_quota": reg.av_quota_status()},
    }
    cache.set(cache_key, result, timeout=5 * 60)
    return result


# ============================================================================ #
# Diversification (correlation + distance correlation)
# ============================================================================ #
@api.get("/diversification", response=DiversificationOut, tags=["portfolio"])
def get_diversification(
    request,
    metric: str = "pearson",
    asset_class: str = "stocks",
    lookback: int = 126,
):
    """Pairwise return correlation matrix + diversification summary stats."""
    if metric not in ("pearson", "distance"):
        raise HttpError(400, "metric must be 'pearson' or 'distance'")
    if asset_class not in ("stocks", "etfs"):
        raise HttpError(400, "asset_class must be 'stocks' or 'etfs'")
    if lookback < 5:
        raise HttpError(400, "lookback must be >= 5")

    reg = get_registry()
    adapter = reg.adapter
    portfolio = _portfolio_dict()

    cache_key = f"div:{reg.name}:{metric}:{asset_class}:{lookback}:{_tx_revision()}"
    cached = cache.get(cache_key)
    if cached is not None:
        return {**cached, "cached": True}

    if metric == "pearson":
        matrix = pf.correlation_matrix(adapter, portfolio, asset_class=asset_class, lookback_days=lookback)
        summary = pf.diversification_summary(matrix)
    else:
        matrix = pf.distance_correlation_matrix(adapter, portfolio, asset_class=asset_class, lookback_days=lookback)
        summary = pf.distance_diversification_summary(matrix)

    payload = {
        "metric": metric,
        "asset_class": asset_class,
        "lookback_days": lookback,
        "tickers": [str(t) for t in matrix.index],
        "matrix": [[ser.safe_float(v) for v in row] for row in matrix.to_numpy()],
        "summary": {str(k): ser.safe(v) for k, v in summary.to_dict().items()},
        "cached": False,
    }
    cache.set(cache_key, {**payload, "cached": False}, timeout=10 * 60)
    return payload


# ============================================================================ #
# Portfolio signals (BUY / HOLD / TRIM for held names)
# ============================================================================ #
@api.get("/portfolio/signals", response=PortfolioSignalsOut, tags=["portfolio"])
def get_portfolio_signals(
    request,
    asset_class: str = "stocks",
    force: bool = False,
):
    """BUY / HOLD / TRIM scores for every portfolio holding in ``asset_class``.

    Same scoring engine as the watchlist (sub-signals: trend, momentum, MACD,
    mean-reversion → composite ∈ [-1, +1] → BUY/HOLD/TRIM), but scoped to names
    the user already owns. Powers the signal-map scatter on Portfolio › Stocks
    so the user can see at a glance which positions are stretched (TRIM
    candidates) vs. oversold (BUY-more candidates) before their next monthly
    deposit. Cached 10 min keyed by (adapter, asset_class, tx revision).
    """
    if asset_class not in ("stocks", "etfs"):
        raise HttpError(400, "asset_class must be 'stocks' or 'etfs'")

    reg = get_registry()
    adapter = reg.adapter
    cache_key = f"pf-signals:{reg.name}:{asset_class}:{_tx_revision()}"

    if not force:
        cached = cache.get(cache_key)
        if cached is not None:
            return {"cached": True, "asset_class": asset_class, "items": cached}

    portfolio = _portfolio_dict()
    # Reuse the watchlist scorer for a per-holding pass -- it's resilient to
    # rate-limits and unknown symbols and returns the exact shape the SPA
    # already knows how to render.
    df = sg.watchlist_signals(adapter, {**portfolio})
    # watchlist_signals walks every ticker in the dict; restrict to the chosen
    # asset class via a post-filter (cheap; the I/O is the per-ticker fetch).
    keep = set(pf.all_tickers(portfolio, asset_class=asset_class))
    df = df.loc[df.index.isin(keep)]

    items = []
    for ticker, row in df.iterrows():
        items.append(
            {
                "ticker": ticker,
                "name": row.get("name", ticker),
                "group": row.get("group", ""),
                "currency": row.get("currency", "USD"),
                "status": row.get("status", "ok"),
                "recommendation": row.get("recommendation", "-"),
                "last_price": ser.safe_float(row.get("last_price")),
                "roc_20d_pct": ser.safe_float(row.get("roc_20d_%")),
                "rsi_14": ser.safe_float(row.get("rsi_14")),
                "zscore_20": ser.safe_float(row.get("zscore_20")),
                "trend": ser.safe_float(row.get("trend")),
                "momentum": ser.safe_float(row.get("momentum")),
                "macd": ser.safe_float(row.get("macd")),
                "mean_reversion": ser.safe_float(row.get("mean_reversion")),
                "score": ser.safe_float(row.get("score")),
                "signal": row.get("signal"),
            }
        )

    cache.set(cache_key, items, timeout=10 * 60)
    return {"cached": False, "asset_class": asset_class, "items": items}


# ============================================================================ #
# Watchlist signals
# ============================================================================ #
@api.get("/watchlist/signals", response=WatchlistOut, tags=["watchlist"])
def get_watchlist_signals(request, force: bool = False):
    """BUY / WATCH / AVOID scores for every name on the watchlist."""
    reg = get_registry()
    adapter = reg.adapter
    cache_key = f"watchlist:{reg.name}"

    if not force:
        cached = cache.get(cache_key)
        if cached is not None:
            return {"cached": True, "items": cached}

    wl = _watchlist_dict()
    df = sg.watchlist_signals(adapter, wl)

    items = []
    for ticker, row in df.iterrows():
        items.append(
            {
                "ticker": ticker,
                "name": row.get("name", ticker),
                "group": row.get("group", ""),
                "currency": row.get("currency", "USD"),
                "status": row.get("status", "ok"),
                "recommendation": row.get("recommendation", "-"),
                "last_price": ser.safe_float(row.get("last_price")),
                "roc_20d_pct": ser.safe_float(row.get("roc_20d_%")),
                "rsi_14": ser.safe_float(row.get("rsi_14")),
                "zscore_20": ser.safe_float(row.get("zscore_20")),
                "trend": ser.safe_float(row.get("trend")),
                "momentum": ser.safe_float(row.get("momentum")),
                "macd": ser.safe_float(row.get("macd")),
                "mean_reversion": ser.safe_float(row.get("mean_reversion")),
                "score": ser.safe_float(row.get("score")),
                "signal": row.get("signal"),
            }
        )

    cache.set(cache_key, items, timeout=10 * 60)
    return {"cached": False, "items": items}


# ============================================================================ #
# Transactions
# ============================================================================ #
@api.get("/transactions", response=list[TransactionOut], tags=["transactions"])
def list_transactions(
    request,
    ticker: Optional[str] = None,
    from_: Optional[date] = Query(None, alias="from"),
    to_: Optional[date] = Query(None, alias="to"),
    action: Optional[str] = None,
):
    qs = Transaction.objects.all()
    if ticker:
        qs = qs.filter(ticker__iexact=ticker)
    if from_:
        qs = qs.filter(date__gte=from_)
    if to_:
        qs = qs.filter(date__lte=to_)
    if action:
        qs = qs.filter(action=action.lower())

    return [_tx_to_dict(t) for t in qs.order_by("-date", "-id")]


@api.get("/transactions/export", tags=["transactions"])
def export_transactions(request):
    """Stream the ledger as a CSV download."""
    df = repo.build_transactions_df()
    csv_text = df.to_csv(index=False)
    resp = HttpResponse(csv_text, content_type="text/csv")
    resp["Content-Disposition"] = 'attachment; filename="transactions.csv"'
    return resp


@api.post(
    "/transactions/import",
    response={200: ImportResultOut, 400: ErrorOut, 422: ImportResultOut},
    tags=["transactions"],
)
def import_transactions(
    request,
    file: UploadedFile = File(...),
    mode: str = "append",
    strict: bool = True,
):
    """Restore transactions from the CSV emitted by ``/transactions/export``.

    Accepted multipart upload (``file=transactions.csv``). Query params:

    - ``mode=append`` (default): add new rows, skip duplicates that match
      ``(date, ticker, action, amount_eur, shares)``.
    - ``mode=replace``: delete every existing Transaction first, then insert.
      Holdings + GroupConfig are preserved.
    - ``strict=true`` (default): if any row fails validation, abort the whole
      import and return 422 with the error list (DB untouched).
    - ``strict=false``: commit valid rows, list rejected rows in the response.

    Missing Holdings are auto-created from the CSV: ``asset_class`` is inferred
    from the ``group`` column (``ETFs`` -> etfs, ``Crypto`` -> crypto, else
    stocks), ``kind`` defaults to ``portfolio``, ``currency`` is taken from the
    row's ``listing_currency``, and ``name`` falls back to the ticker (rename
    later in the SPA).
    """
    from io import BytesIO

    if mode not in ("append", "replace"):
        raise HttpError(400, "mode must be 'append' or 'replace'")

    expected_cols = list(pf.TRANSACTION_COLUMNS)

    try:
        df = pd.read_csv(BytesIO(file.read()), dtype=str)
    except Exception as e:
        raise HttpError(400, f"could not parse CSV: {e}")

    if list(df.columns) != expected_cols:
        missing = [c for c in expected_cols if c not in df.columns]
        extra = [c for c in df.columns if c not in expected_cols]
        bits = []
        if missing:
            bits.append(f"missing columns: {missing}")
        if extra:
            bits.append(f"unexpected columns: {extra}")
        raise HttpError(
            400,
            "CSV header does not match the export format. "
            + " ; ".join(bits)
            + f". Expected order: {expected_cols}",
        )

    # ---- row-level validation + coercion -----------------------------------
    valid_rows: list[dict] = []
    errors: list[dict] = []
    for i, raw in df.iterrows():
        try:
            action = str(raw["action"]).strip().lower()
            if action not in ("buy", "sell"):
                raise ValueError(f"action must be 'buy' or 'sell' (got {action!r})")
            date_val = pd.to_datetime(raw["date"]).date()
            row = {
                "date": date_val,
                "ticker": str(raw["ticker"]).strip(),
                "group": str(raw["group"]).strip(),
                "action": action,
                "amount_eur": repo._to_decimal(raw["amount_eur"]) or Decimal("0"),
                "price_local": repo._to_decimal(raw["price_local"]) or Decimal("0"),
                "listing_currency": str(raw["listing_currency"]).strip().upper(),
                "eur_per_local": repo._to_decimal(raw["eur_per_local"]) or Decimal("1"),
                "shares": repo._to_decimal(raw["shares"]) or Decimal("0"),
                "fee_eur": repo._to_decimal(raw.get("fee_eur") or 0) or Decimal("0"),
                "note": str(raw.get("note") or "") if not pd.isna(raw.get("note")) else "",
            }
            if not row["ticker"]:
                raise ValueError("ticker is empty")
            valid_rows.append(row)
        except Exception as e:
            errors.append({"row_index": int(i), "message": str(e)})

    # If we're strict and any row failed -> 422, no DB changes.
    if strict and errors:
        return 422, {
            "mode": mode,
            "imported": 0,
            "skipped": 0,
            "errors": errors,
            "holdings_created": [],
            "strict": strict,
        }

    # ---- DB mutations (atomic) --------------------------------------------
    imported = 0
    skipped = 0
    holdings_created: list[str] = []

    with db_transaction.atomic():
        if mode == "replace":
            Transaction.objects.all().delete()

        # Pre-existing Holdings so we know which tickers need auto-create.
        existing_tickers = set(
            Holding.objects.filter(kind=HoldingKind.PORTFOLIO).values_list(
                "ticker", flat=True
            )
        )

        # Index of existing rows for append-mode dedup.
        existing_keys: set[tuple] = set()
        if mode == "append":
            for tx in Transaction.objects.values(
                "date", "ticker", "action", "amount_eur", "shares"
            ):
                existing_keys.add(
                    (
                        tx["date"],
                        tx["ticker"],
                        tx["action"],
                        tx["amount_eur"],
                        tx["shares"],
                    )
                )

        for row in valid_rows:
            # Auto-create Holding (+ GroupConfig) if the ticker is unknown.
            if row["ticker"] not in existing_tickers:
                asset_class = repo.infer_asset_class(row["group"])
                GroupConfig.objects.get_or_create(
                    asset_class=asset_class,
                    name=row["group"],
                    defaults={"description": ""},
                )
                Holding.objects.create(
                    kind=HoldingKind.PORTFOLIO,
                    asset_class=asset_class,
                    group=row["group"],
                    ticker=row["ticker"],
                    name=row["ticker"],   # placeholder; user renames in the SPA
                    currency=row["listing_currency"] or "USD",
                )
                existing_tickers.add(row["ticker"])
                holdings_created.append(row["ticker"])

            if mode == "append":
                key = (
                    row["date"],
                    row["ticker"],
                    row["action"],
                    row["amount_eur"],
                    row["shares"],
                )
                if key in existing_keys:
                    skipped += 1
                    continue
                existing_keys.add(key)

            Transaction.objects.create(**row)
            imported += 1

    audit(
        request,
        "/transactions/import",
        "POST",
        {
            "mode": mode,
            "strict": strict,
            "imported": imported,
            "skipped": skipped,
            "errors": len(errors),
            "holdings_created": holdings_created,
            "filename": file.name,
        },
    )

    return 200, {
        "mode": mode,
        "imported": imported,
        "skipped": skipped,
        "errors": errors,
        "holdings_created": holdings_created,
        "strict": strict,
    }


def _tx_to_dict(t: Transaction) -> dict:
    """One Transaction row -> the TransactionOut shape."""
    return {
        "id": t.id,
        "date": t.date.isoformat(),
        "ticker": t.ticker,
        "group": t.group,
        "action": t.action,
        "amount_eur": float(t.amount_eur),
        "price_local": float(t.price_local),
        "listing_currency": t.listing_currency,
        "eur_per_local": float(t.eur_per_local),
        "shares": float(t.shares),
        "fee_eur": float(t.fee_eur),
        "note": t.note or "",
    }


@api.post(
    "/transactions",
    response={201: TransactionOut, 400: ErrorOut},
    tags=["transactions"],
)
def create_transaction(request, body: TransactionCreate):
    """Log a buy or sell. The caller submits **either** ``amount_eur`` (we
    compute shares from the EOD close + FX on ``date``) **or** ``shares``
    (we compute the EUR proceeds from the same EOD data). Either way the
    sign convention on disk is positive for buy / negative for sell.
    """
    action = (body.action or "buy").lower()
    if action not in ("buy", "sell"):
        return 400, {"detail": "action must be 'buy' or 'sell'"}

    # Exactly one of amount_eur / shares must be set, and it must be
    # positive. The sign comes from `action`.
    has_amount = body.amount_eur is not None
    has_shares = body.shares is not None
    if has_amount == has_shares:
        return 400, {
            "detail": (
                "Provide exactly one of amount_eur or shares -- "
                "amount_eur for EUR-mode entry, shares for units-mode entry."
            )
        }
    if has_amount and body.amount_eur <= 0:
        return 400, {"detail": "amount_eur must be > 0"}
    if has_shares and body.shares <= 0:
        return 400, {"detail": "shares must be > 0"}

    ticker = body.ticker.strip().upper() if body.ticker else ""
    if not ticker:
        return 400, {"detail": "ticker is required"}

    holding = (
        Holding.objects.filter(ticker=ticker, kind=HoldingKind.PORTFOLIO).first()
    )
    if holding is None:
        return 400, {
            "detail": (
                f"No portfolio holding for ticker '{ticker}'. "
                "Add it via the Add-Stock flow first."
            )
        }

    # Parse date (str ISO -> date).
    try:
        trade_date = date.fromisoformat(body.date)
    except (TypeError, ValueError):
        return 400, {"detail": f"date must be YYYY-MM-DD (got {body.date!r})"}

    adapter = get_registry().adapter
    listing_currency = (body.listing_currency or holding.currency or "USD").upper()

    try:
        if has_amount:
            # EUR -> shares. Existing path, unchanged.
            est = pf.estimate_shares(
                adapter, ticker, trade_date, body.amount_eur,
                listing_currency=listing_currency,
            )
            absolute_eur = float(body.amount_eur)
            absolute_shares = float(est["shares"])
        else:
            # shares -> EUR (units-mode sell, or buy by share count).
            # estimate_shares with a sentinel EUR of 1.0 cheaply recovers
            # price_local + eur_per_local on the trade date, then we
            # back out the EUR proceeds for the user's share count.
            est = pf.estimate_shares(
                adapter, ticker, trade_date, 1.0,
                listing_currency=listing_currency,
            )
            price_eur = est["price_local"] * est["eur_per_local"]
            absolute_shares = float(body.shares)
            absolute_eur = absolute_shares * price_eur
    except DataUnavailableError as e:
        return 400, {"detail": f"price unavailable: {e}"}

    sign = -1 if action == "sell" else 1
    t = Transaction.objects.create(
        date=trade_date,
        ticker=ticker,
        group=holding.group,
        action=action,
        amount_eur=Decimal(str(absolute_eur)) * sign,
        price_local=Decimal(str(est["price_local"])),
        listing_currency=est["listing_currency"],
        eur_per_local=Decimal(str(est["eur_per_local"])),
        shares=Decimal(str(absolute_shares)) * sign,
        fee_eur=Decimal(str(body.fee_eur or 0)),
        note=body.note or "",
    )
    audit(request, "/transactions", "POST", body.dict() | {"id": t.id})
    return 201, _tx_to_dict(t)


@api.patch("/transactions/{tx_id}", response=TransactionOut, tags=["transactions"])
def update_transaction(request, tx_id: int, body: TransactionPatch):
    """Edit just the ``note`` and/or ``fee_eur`` on an existing transaction.
    Other fields (date/ticker/amount/shares/price/FX) are immutable -- delete
    and re-create if you need to change them."""
    t = Transaction.objects.filter(id=tx_id).first()
    if t is None:
        raise HttpError(404, f"Transaction {tx_id} not found")
    if body.note is not None:
        t.note = body.note
    if body.fee_eur is not None:
        t.fee_eur = Decimal(str(body.fee_eur))
    t.save()
    audit(request, f"/transactions/{tx_id}", "PATCH", body.dict())
    return _tx_to_dict(t)


@api.delete(
    "/transactions/{tx_id}",
    response={204: None, 404: ErrorOut},
    tags=["transactions"],
)
def delete_transaction(request, tx_id: int):
    deleted, _ = Transaction.objects.filter(id=tx_id).delete()
    if not deleted:
        return 404, {"detail": f"Transaction {tx_id} not found"}
    audit(request, f"/transactions/{tx_id}", "DELETE", {"id": tx_id})
    return 204, None


# ============================================================================ #
# Holdings + Groups (mutating, Phase 6)
# ============================================================================ #
@api.post(
    "/holdings",
    response={201: HoldingOut, 400: ErrorOut},
    tags=["holdings"],
)
def create_holding(request, body: HoldingCreate):
    """Add a new portfolio or watchlist holding. Atomically logs the initial
    Transaction too if ``initial_amount_eur`` is given (portfolio only) -- the
    Holding is rolled back if the price fetch fails."""
    kind = (body.kind or "").lower()
    asset_class = (body.asset_class or "").lower()
    if kind not in (HoldingKind.PORTFOLIO, HoldingKind.WATCHLIST):
        return 400, {"detail": "kind must be 'portfolio' or 'watchlist'"}
    if asset_class not in (AssetClass.STOCKS, AssetClass.ETFS):
        return 400, {"detail": "asset_class must be 'stocks' or 'etfs'"}

    ticker = (body.ticker or "").strip().upper()
    if not ticker:
        return 400, {"detail": "ticker is required"}
    name = (body.name or "").strip()
    if not name:
        return 400, {"detail": "name is required"}
    currency = (body.currency or "USD").strip().upper()

    # ETFs always live in the single "ETFs" group.
    group_name = "ETFs" if asset_class == AssetClass.ETFS else (body.group or "").strip()
    if not group_name:
        return 400, {"detail": "group is required for stocks"}

    if Holding.objects.filter(kind=kind, ticker=ticker).exists():
        return 400, {"detail": f"{ticker} is already in your {kind}"}

    try:
        with db_transaction.atomic():
            # 1) Group config (get-or-create; update target_weight/description if newly creating).
            group_obj, created_group = GroupConfig.objects.get_or_create(
                asset_class=asset_class,
                name=group_name,
                defaults={
                    "description": (body.group_description or "") or "",
                    "target_weight": (
                        Decimal(str(body.target_weight))
                        if body.target_weight is not None
                        else None
                    ),
                },
            )

            # 2) Holding row.
            holding = Holding.objects.create(
                kind=kind,
                asset_class=asset_class,
                group=group_name,
                ticker=ticker,
                name=name,
                currency=currency,
            )

            # 3) Optional initial Transaction (portfolio only).
            tx_dict: Optional[dict] = None
            if (
                kind == HoldingKind.PORTFOLIO
                and body.initial_amount_eur is not None
                and body.initial_amount_eur > 0
            ):
                try:
                    trade_date = (
                        date.fromisoformat(body.initial_date)
                        if body.initial_date
                        else date.today()
                    )
                except ValueError:
                    raise HttpError(400, f"initial_date must be YYYY-MM-DD")

                adapter = get_registry().adapter
                try:
                    est = pf.estimate_shares(
                        adapter,
                        ticker,
                        trade_date,
                        body.initial_amount_eur,
                        listing_currency=currency,
                    )
                except DataUnavailableError as e:
                    raise HttpError(400, f"Could not fetch price for {ticker}: {e}")

                tx = Transaction.objects.create(
                    date=trade_date,
                    ticker=ticker,
                    group=group_name,
                    action="buy",
                    amount_eur=Decimal(str(body.initial_amount_eur)),
                    price_local=Decimal(str(est["price_local"])),
                    listing_currency=est["listing_currency"],
                    eur_per_local=Decimal(str(est["eur_per_local"])),
                    shares=Decimal(str(est["shares"])),
                    fee_eur=Decimal(str(body.initial_fee_eur or 0)),
                    note=(body.initial_note or "initial buy"),
                )
                tx_dict = _tx_to_dict(tx)
    except HttpError:
        raise
    except Exception as e:  # pragma: no cover -- defensive
        return 400, {"detail": f"failed to create holding: {e}"}

    audit(request, "/holdings", "POST", body.dict() | {"id": holding.id})
    return 201, {
        "id": holding.id,
        "kind": holding.kind,
        "asset_class": holding.asset_class,
        "group": holding.group,
        "ticker": holding.ticker,
        "name": holding.name,
        "currency": holding.currency,
        "transaction": tx_dict,
    }


@api.delete(
    "/holdings/{ticker}",
    response={204: None, 400: ErrorOut, 404: ErrorOut},
    tags=["holdings"],
)
def delete_holding(request, ticker: str, kind: str = HoldingKind.PORTFOLIO):
    """Remove a holding. Refuses if portfolio holding has any transactions
    (delete transactions first); watchlist holdings can be removed freely."""
    h = Holding.objects.filter(ticker=ticker.upper(), kind=kind).first()
    if h is None:
        return 404, {"detail": f"{kind}/{ticker} not found"}
    if kind == HoldingKind.PORTFOLIO and Transaction.objects.filter(ticker=ticker.upper()).exists():
        return 400, {
            "detail": (
                f"{ticker} has open transactions; delete those first or use the "
                "Transactions view."
            )
        }
    h.delete()
    audit(request, f"/holdings/{ticker}", "DELETE", {"ticker": ticker.upper(), "kind": kind})
    return 204, None


def _group_to_dict(obj: GroupConfig) -> dict:
    """Serialise a GroupConfig + count how many holdings reference it (any kind)."""
    return {
        "asset_class": obj.asset_class,
        "name": obj.name,
        "description": obj.description or "",
        "target_weight": float(obj.target_weight) if obj.target_weight is not None else None,
        "holdings_count": Holding.objects.filter(
            asset_class=obj.asset_class, group=obj.name
        ).count(),
    }


@api.get("/groups", response=list[GroupOut], tags=["holdings"])
def list_groups(request, asset_class: Optional[str] = None):
    """List GroupConfig rows. Optional ``asset_class`` filter
    (``stocks`` or ``etfs``). Each row carries ``holdings_count`` so the
    Manage-Groups modal can show usage and gate destructive actions."""
    qs = GroupConfig.objects.all()
    if asset_class:
        ac = asset_class.lower()
        if ac not in (AssetClass.STOCKS, AssetClass.ETFS):
            raise HttpError(400, "asset_class must be 'stocks' or 'etfs'")
        qs = qs.filter(asset_class=ac)
    return [_group_to_dict(g) for g in qs.order_by("asset_class", "name")]


@api.post(
    "/groups",
    response={201: GroupOut, 400: ErrorOut},
    tags=["holdings"],
)
def upsert_group(request, body: GroupCreate):
    """Create or update a (asset_class, group_name) config (description +
    optional target weight). Idempotent."""
    asset_class = (body.asset_class or "").lower()
    if asset_class not in (AssetClass.STOCKS, AssetClass.ETFS):
        return 400, {"detail": "asset_class must be 'stocks' or 'etfs'"}
    name = (body.name or "").strip()
    if not name:
        return 400, {"detail": "name is required"}
    if asset_class == AssetClass.ETFS and name != "ETFs":
        return 400, {"detail": "ETF group must be named 'ETFs'"}

    obj, created = GroupConfig.objects.get_or_create(
        asset_class=asset_class,
        name=name,
        defaults={
            "description": (body.description or ""),
            "target_weight": (
                Decimal(str(body.target_weight)) if body.target_weight is not None else None
            ),
        },
    )
    if not created:
        if body.description is not None:
            obj.description = body.description
        if body.target_weight is not None:
            obj.target_weight = Decimal(str(body.target_weight))
        obj.save()

    audit(request, "/groups", "POST", body.dict() | {"created": created})
    return 201, _group_to_dict(obj)


@api.patch(
    "/groups/{asset_class}/{name}",
    response={200: GroupOut, 400: ErrorOut, 404: ErrorOut},
    tags=["holdings"],
)
def update_group(request, asset_class: str, name: str, body: GroupPatch):
    """Update description / target_weight on an existing group.

    Pass ``clear_target_weight=true`` to wipe the target back to NULL (e.g. a
    sleeve you no longer want to rebalance against)."""
    ac = (asset_class or "").lower()
    if ac not in (AssetClass.STOCKS, AssetClass.ETFS):
        return 400, {"detail": "asset_class must be 'stocks' or 'etfs'"}
    obj = GroupConfig.objects.filter(asset_class=ac, name=name).first()
    if obj is None:
        return 404, {"detail": f"group '{name}' (asset_class={ac}) not found"}

    if body.description is not None:
        obj.description = body.description
    if body.clear_target_weight:
        obj.target_weight = None
    elif body.target_weight is not None:
        obj.target_weight = Decimal(str(body.target_weight))
    obj.save()

    audit(
        request,
        f"/groups/{ac}/{name}",
        "PATCH",
        body.dict() | {"asset_class": ac, "name": name},
    )
    return 200, _group_to_dict(obj)


@api.delete(
    "/groups/{asset_class}/{name}",
    response={204: None, 400: ErrorOut, 404: ErrorOut},
    tags=["holdings"],
)
def delete_group(request, asset_class: str, name: str):
    """Remove a group. Refuses if any Holding still references it -- move/delete
    those first. Also refuses to delete the canonical 'ETFs' group while ETF
    holdings exist; ETF holdings have nowhere else to live."""
    ac = (asset_class or "").lower()
    if ac not in (AssetClass.STOCKS, AssetClass.ETFS):
        return 400, {"detail": "asset_class must be 'stocks' or 'etfs'"}
    obj = GroupConfig.objects.filter(asset_class=ac, name=name).first()
    if obj is None:
        return 404, {"detail": f"group '{name}' (asset_class={ac}) not found"}

    in_use = Holding.objects.filter(asset_class=ac, group=name).count()
    if in_use:
        return 400, {
            "detail": (
                f"group '{name}' is used by {in_use} holding(s); reassign or "
                "delete those first"
            )
        }
    obj.delete()
    audit(request, f"/groups/{ac}/{name}", "DELETE", {"asset_class": ac, "name": name})
    return 204, None


# ============================================================================ #
# Instruments (search / quote / history / indicators / score / estimate-shares)
# ============================================================================ #
# yfinance returns quoteType values like "EQUITY", "ETF", "MUTUALFUND", "INDEX",
# "CURRENCY", "CRYPTOCURRENCY". Alpha Vantage SYMBOL_SEARCH returns human-readable
# strings like "Equity", "ETF", "Mutual Fund". Normalise both into the two
# buckets the SPA uses (``stocks`` / ``etfs``) for filtering.
_STOCK_TYPE_TOKENS = {"equity", "stock", "common stock", "mutualfund", "mutual fund"}
_ETF_TYPE_TOKENS = {"etf", "exchange traded fund", "exchangetradedfund"}


def _matches_asset_class(raw_type: Optional[str], wanted: str) -> bool:
    """Decide whether a search-hit's provider type string belongs to the
    ``stocks`` or ``etfs`` bucket the UI is asking for."""
    if not raw_type:
        # Provider didn't tell us -- be permissive (don't drop the hit).
        return True
    token = raw_type.strip().lower().replace("-", "").replace("_", "")
    if wanted == "etfs":
        return token in _ETF_TYPE_TOKENS or "etf" in token
    if wanted == "stocks":
        # Anything that's not clearly an ETF passes -- equities, mutual funds,
        # ADRs, etc. Indices/crypto/futures/options/warrants are excluded:
        # option-chain symbols in particular (e.g. "AAPL260618C00300000") are
        # extremely noisy in yfinance's Search results.
        if token in _ETF_TYPE_TOKENS or "etf" in token:
            return False
        if token in {
            "index",
            "currency",
            "cryptocurrency",
            "crypto",
            "future",
            "option",
            "warrant",
            "right",
        }:
            return False
        return True
    return True


@api.get("/instruments/search", response=list[InstrumentSearchHit], tags=["instruments"])
def search_instruments(request, q: str, type: Optional[str] = None):
    """Resolve a company name or partial ticker through the active adapter's
    symbol-search endpoint (yfinance ``Search`` or Alpha Vantage ``SYMBOL_SEARCH``).

    The optional ``type`` parameter filters hits to a single asset class --
    ``stocks`` (equity + mutual funds) or ``etfs`` -- so the Add-Stock modal can
    show only ETFs when adding to the ETFs sleeve and the global nav bar can
    skip non-stock noise."""
    q = (q or "").strip()
    if len(q) < 2:
        return []
    type_filter = (type or "").strip().lower() or None
    if type_filter not in (None, "stocks", "etfs"):
        raise HttpError(400, "type must be 'stocks' or 'etfs'")

    cache_key = f"search:{get_registry().name}:{type_filter or 'all'}:{q.lower()}"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    adapter = get_registry().adapter
    df = adapter.search_symbols(q)
    out = []
    if df is not None and not df.empty:
        for _, r in df.iterrows():
            raw_type = (r.get("type") or "") or None
            if type_filter and not _matches_asset_class(raw_type, type_filter):
                continue
            out.append(
                {
                    "symbol": str(r.get("symbol") or ""),
                    "name": (r.get("name") or "") or None,
                    "type": raw_type,
                    "region": (r.get("region") or "") or None,
                    "currency": (r.get("currency") or "") or None,
                }
            )
    cache.set(cache_key, out, timeout=60 * 60)
    return out


@api.get("/instruments/{ticker}/quote", response=InstrumentQuoteOut, tags=["instruments"])
def get_instrument_quote(request, ticker: str):
    """Latest quote (price, change %, volume, last trading day)."""
    adapter = get_registry().adapter
    q = adapter.get_quote(ticker)
    return {
        "symbol": ticker,
        "price": ser.safe_float(q.get("price")),
        "open": ser.safe_float(q.get("open")),
        "high": ser.safe_float(q.get("high")),
        "low": ser.safe_float(q.get("low")),
        "previous_close": ser.safe_float(q.get("previous close")),
        "change": ser.safe_float(q.get("change")),
        "change_percent": ser.safe_float(q.get("change percent")),
        "volume": ser.safe_float(q.get("volume")),
        "latest_trading_day": q.get("latest trading day"),
    }


@api.get("/instruments/{ticker}/history", response=HistoryFrameOut, tags=["instruments"])
def get_instrument_history(
    request,
    ticker: str,
    interval: str = "daily",
    range_: str = Query("1y", alias="range"),
):
    """OHLCV history. ``interval`` in {daily, weekly, monthly}; ``range`` slices
    the trailing window (``1mo|3mo|6mo|1y|2y|5y|max``)."""
    if interval not in ("daily", "weekly", "monthly"):
        raise HttpError(400, "interval must be daily/weekly/monthly")
    adapter = get_registry().adapter
    if interval == "daily":
        outputsize = "full" if range_ == "max" else "compact"
        df = adapter.get_daily(ticker, outputsize=outputsize)
    else:
        df = adapter.get_history(ticker, interval=interval)
    df = _slice_range(df, range_)
    return {
        "symbol": ticker,
        "interval": interval,
        "dates": [d.date().isoformat() if hasattr(d, "date") else str(d) for d in df.index],
        "open": [ser.safe_float(v) for v in df["open"]],
        "high": [ser.safe_float(v) for v in df["high"]],
        "low": [ser.safe_float(v) for v in df["low"]],
        "close": [ser.safe_float(v) for v in df["close"]],
        "volume": [ser.safe_float(v) for v in df["volume"]] if "volume" in df.columns else [],
    }


@api.get("/instruments/{ticker}/indicators", response=IndicatorsOut, tags=["instruments"])
def get_instrument_indicators(
    request, ticker: str, range_: str = Query("1y", alias="range")
):
    """Full technical indicator panel (price + SMA/EMA + RSI + MACD + Bollinger + z-score)."""
    adapter = get_registry().adapter
    df = adapter.get_daily(ticker)
    df = m.add_indicators(df)
    df = _slice_range(df, range_)
    return {
        "symbol": ticker,
        "dates": [d.date().isoformat() if hasattr(d, "date") else str(d) for d in df.index],
        "columns": [str(c) for c in df.columns],
        "data": [[ser.safe_float(v) for v in row] for row in df.itertuples(index=False, name=None)],
    }


@api.get("/instruments/{ticker}/score", response=ScoreOut, tags=["instruments"])
def get_instrument_score(request, ticker: str):
    """Composite BUY/HOLD/TRIM quant score for a single ticker."""
    adapter = get_registry().adapter
    prices = adapter.get_daily(ticker)["close"]
    scored = sg.score_series(prices)
    return {
        "ticker": ticker,
        "last_price": ser.safe_float(scored.get("price_usd")),
        "roc_20d_pct": ser.safe_float(scored.get("roc_20d_%")),
        "rsi_14": ser.safe_float(scored.get("rsi_14")),
        "zscore_20": ser.safe_float(scored.get("zscore_20")),
        "trend": ser.safe_float(scored.get("trend")),
        "momentum": ser.safe_float(scored.get("momentum")),
        "macd": ser.safe_float(scored.get("macd")),
        "mean_reversion": ser.safe_float(scored.get("mean_reversion")),
        "score": ser.safe_float(scored.get("score")),
        "signal": scored.get("signal"),
    }


@api.get("/instruments/{ticker}/estimate-shares", response=EstimateSharesOut, tags=["instruments"])
def estimate_shares(
    request,
    ticker: str,
    amount_eur: float,
    on: date = Query(..., description="Trade date"),
    listing_currency: str = "USD",
):
    """Preview shares + USD/EUR FX for a hypothetical buy on a given date.

    Powers the live preview in the Add-Investment modal (Phase 5).
    """
    if amount_eur <= 0:
        raise HttpError(400, "amount_eur must be > 0")
    adapter = get_registry().adapter
    est = pf.estimate_shares(adapter, ticker, on, amount_eur, listing_currency=listing_currency)
    return {
        "ticker": ticker,
        "date": on.isoformat(),
        "amount_eur": float(amount_eur),
        "price_local": float(est["price_local"]),
        "listing_currency": est["listing_currency"],
        "eur_per_local": float(est["eur_per_local"]),
        "shares": float(est["shares"]),
        "price_eur": float(est["price_local"]) * float(est["eur_per_local"]),
    }


@api.get(
    "/instruments/{ticker}/estimate-proceeds",
    response=EstimateProceedsOut,
    tags=["instruments"],
)
def estimate_proceeds(
    request,
    ticker: str,
    shares: float,
    on: date = Query(..., description="Trade date"),
    listing_currency: str = "USD",
):
    """Mirror of estimate-shares but units -> EUR. Powers the sell modal's
    live preview when the user enters share count instead of EUR amount.
    """
    if shares <= 0:
        raise HttpError(400, "shares must be > 0")
    adapter = get_registry().adapter
    est = pf.estimate_shares(adapter, ticker, on, 1.0, listing_currency=listing_currency)
    price_eur = float(est["price_local"]) * float(est["eur_per_local"])
    return {
        "ticker": ticker,
        "date": on.isoformat(),
        "shares": float(shares),
        "price_local": float(est["price_local"]),
        "listing_currency": est["listing_currency"],
        "eur_per_local": float(est["eur_per_local"]),
        "amount_eur": float(shares) * price_eur,
        "price_eur": price_eur,
    }


@api.get(
    "/holdings/{ticker}/position",
    response=HoldingPositionOut,
    tags=["holdings"],
)
def get_holding_position(request, ticker: str):
    """Return the current net position (signed share count + cumulative EUR
    cost basis) for ``ticker``. Powers:

      * "Sell all" prefill in the Add-Investment modal,
      * the conditional "+ Log sell" button on the Stock single-page view
        (only rendered when shares > 0).

    Returns 0/0 when the ticker has no Transaction rows -- callers should
    not treat that as a 404 (a brand-new holding without an initial buy
    has the same shape and should still surface in the SPA).
    """
    sym = (ticker or "").strip().upper()
    shares, cost = repo.get_current_position(sym)
    return {
        "ticker": sym,
        "shares": float(shares),
        "cost_eur": float(cost),
    }


# ============================================================================ #
# Settings
# ============================================================================ #
def _av_api_key() -> Optional[str]:
    """Return the Alpha Vantage API key, checking (in order): the DB Setting
    row written via PUT /settings, the OS environment, and finally the .env
    file picked up by ``autoquant.client.load_api_key``."""
    import os
    from .models import Setting

    row = Setting.objects.filter(key="av_api_key").first()
    if row is not None and row.value:
        return str(row.value)
    env = os.environ.get("AV_API_KEY")
    if env:
        return env
    try:
        from autoquant.client import load_api_key

        return load_api_key()
    except Exception:
        return None


@api.get("/settings", response=SettingsOut, tags=["settings"])
def get_settings(request):
    reg = get_registry()
    return {
        "adapter": reg.name,
        "base_currency": "EUR",
        "av_quota": reg.av_quota_status(),
        "av_api_key_set": _av_api_key() is not None,
    }


@api.put("/settings", response={200: SettingsOut, 400: ErrorOut}, tags=["settings"])
def update_settings(request, body: SettingsUpdate):
    """Persist a new AV API key and/or hot-swap the active adapter."""
    from .models import Setting

    reg = get_registry()

    # Persist AV API key if provided. Empty string clears it.
    if body.av_api_key is not None:
        cleaned = body.av_api_key.strip()
        if cleaned:
            Setting.objects.update_or_create(key="av_api_key", defaults={"value": cleaned})
        else:
            Setting.objects.filter(key="av_api_key").delete()

    # Adapter swap if requested.
    if body.adapter:
        name = body.adapter.lower().strip()
        if name in ("yfinance", "yahoo", "yf"):
            try:
                reg.swap("yfinance")
            except Exception as exc:
                return 400, {"detail": f"yfinance adapter failed: {exc}"}
        elif name in ("alphavantage", "alpha", "av", "alpha_vantage"):
            api_key = _av_api_key()
            if not api_key:
                return 400, {
                    "detail": (
                        "Alpha Vantage adapter needs an API key. Save one with "
                        "av_api_key first."
                    )
                }
            try:
                reg.swap("alphavantage", api_key=api_key)
            except Exception as exc:
                return 400, {"detail": f"alphavantage adapter failed: {exc}"}
        else:
            return 400, {"detail": f"unknown adapter: {body.adapter}"}

        # Adapter change can invalidate cached snapshots keyed by adapter name.
        cache.clear()

    audit(request, "/settings", "PUT", body.dict())
    return 200, {
        "adapter": reg.name,
        "base_currency": "EUR",
        "av_quota": reg.av_quota_status(),
        "av_api_key_set": _av_api_key() is not None,
    }


@api.post("/cache/clear", response=CacheClearedOut, tags=["settings"])
def clear_cache(request):
    """Wipe Django's view cache and the adapter's in-memory price cache.

    Backs the top-bar "Refresh prices" button: the SPA POSTs here, gets back
    the adapter name + ISO timestamp, then bumps its ``pricesRevision`` store
    so every mounted view refetches. Returning a body (rather than 204) lets
    the SPA render an "Updated 14:32" badge after a successful refresh.
    """
    from datetime import datetime, timezone

    cache.clear()
    reg = get_registry()
    reg.clear_cache()
    payload = {"adapter": reg.name, "at": datetime.now(timezone.utc).isoformat()}
    audit(request, "/cache/clear", "POST", payload)
    return payload

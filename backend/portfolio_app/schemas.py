"""Response schemas for the API (django-ninja uses them for OpenAPI + validation).

Kept intentionally permissive (``Optional`` everywhere a value may be missing
because of NaN / empty-history / quota-limited fetches) so the API can degrade
gracefully without exploding the schema validator.
"""

from __future__ import annotations

from typing import Any, Optional

from ninja import Schema


# --------------------------------------------------------------------------- #
# Portfolio snapshot
# --------------------------------------------------------------------------- #
class PortfolioPosition(Schema):
    ticker: str
    name: str
    group: str
    currency: str
    shares: float
    invested_eur: float
    fees_eur: float = 0.0
    price_local: Optional[float] = None
    price_eur: Optional[float] = None
    value_eur: Optional[float] = None
    cost_eur: float
    pnl_eur: Optional[float] = None
    return_pct: Optional[float] = None
    weight: Optional[float] = None


class GroupSummary(Schema):
    group: str
    value_eur: float
    cost_eur: float
    pnl_eur: float
    return_pct: Optional[float] = None
    weight: Optional[float] = None
    target_weight: Optional[float] = None
    drift_pct: Optional[float] = None


class PortfolioTotals(Schema):
    value_eur: float
    cost_eur: float
    pnl_eur: float
    return_pct: Optional[float] = None


class PortfolioSnapshotOut(Schema):
    asset_class: str
    positions: list[PortfolioPosition]
    by_group: list[GroupSummary]
    totals: PortfolioTotals
    fx_rates: dict[str, float]


# --------------------------------------------------------------------------- #
# Value history
# --------------------------------------------------------------------------- #
class ValueHistoryOut(Schema):
    asset_class: str
    dates: list[str]
    by_ticker: dict[str, list[Optional[float]]]
    by_group: dict[str, list[Optional[float]]]
    total: list[Optional[float]]
    invested: list[Optional[float]]


# --------------------------------------------------------------------------- #
# Dashboard
# --------------------------------------------------------------------------- #
class AdapterStatus(Schema):
    name: str
    av_quota: dict[str, Any]


class TopMover(Schema):
    ticker: str
    name: str
    asset_class: str
    change_pct: float
    last_close: float
    value_eur: Optional[float] = None


class Sparkline(Schema):
    dates: list[str]
    values: list[Optional[float]]


class DashboardOut(Schema):
    """Cross-sleeve dashboard payload. ``by_class`` is keyed by asset class
    (stocks / etfs / crypto) so the SPA can render KPI cards by iteration
    instead of hardcoding sleeves. ``combined`` aggregates every sleeve.
    """

    by_class: dict[str, PortfolioTotals]
    combined: PortfolioTotals
    sparkline: Sparkline
    top_movers: list[TopMover]
    adapter: AdapterStatus


# --------------------------------------------------------------------------- #
# Transactions
# --------------------------------------------------------------------------- #
class TransactionOut(Schema):
    id: int
    date: str
    ticker: str
    group: str
    action: str
    amount_eur: float
    price_local: float
    listing_currency: str
    eur_per_local: float
    shares: float
    fee_eur: float
    note: str
    swap_group_id: Optional[str] = None   # set on both legs of a swap; else None


class TransactionCreate(Schema):
    """Body for ``POST /api/transactions``.

    Caller must set **exactly one** of ``amount_eur`` or ``shares``:

    - ``amount_eur`` (legacy buy / EUR-mode sell): backend looks up the
      EOD close + FX on ``date`` and computes ``shares``.
    - ``shares`` (units-mode sell or buy by share count): backend looks up
      the EOD close + FX on ``date`` and computes ``amount_eur``.

    The frontend's sign convention: always positive. The endpoint flips the
    sign on disk for sells.
    """

    date: str                                # ISO YYYY-MM-DD
    ticker: str
    action: str = "buy"                      # 'buy' or 'sell'
    amount_eur: Optional[float] = None       # positive; one-of with shares
    shares: Optional[float] = None           # positive; one-of with amount_eur
    listing_currency: str = "USD"
    fee_eur: float = 0.0
    note: str = ""


class TransactionPatch(Schema):
    """Body for ``PATCH /api/transactions/{id}``. Only mutable fields."""

    note: Optional[str] = None
    fee_eur: Optional[float] = None


class SwapCreate(Schema):
    """Body for ``POST /api/transactions/swap``.

    Models a single coin-for-coin swap (e.g. 1000 USDC-EUR -> 0.025 BTC-EUR)
    as two linked Transaction rows: a sell of ``from_ticker`` and a buy of
    ``to_ticker``, both tagged with one shared ``swap_group_id``.

    Both holdings must already exist (add them via the Add-Coin flow first).
    ``eur_value`` is the EUR value of the swapped amount; if omitted the
    backend prices the ``from`` leg from EOD data on ``date``.
    """

    date: str                            # ISO YYYY-MM-DD
    from_ticker: str
    from_amount: float                   # units of from_ticker leaving the wallet (> 0)
    from_currency: str = "EUR"
    to_ticker: str
    to_amount: float                     # units of to_ticker entering the wallet (> 0)
    to_currency: str = "EUR"
    eur_value: Optional[float] = None    # EUR value of the swap; computed if omitted
    fee_eur: float = 0.0
    note: str = ""


class SwapResultOut(Schema):
    """Both legs of a created swap, plus the shared group id."""

    swap_group_id: str
    sell: TransactionOut                 # the from_ticker leg (negative shares)
    buy: TransactionOut                  # the to_ticker leg (positive shares)


# --------------------------------------------------------------------------- #
# CSV import
# --------------------------------------------------------------------------- #
class RowImportError(Schema):
    """One bad row, surfaced by ``POST /api/transactions/import``."""

    row_index: int           # 0-based, excluding the header row
    message: str


class ImportResultOut(Schema):
    """Summary returned by ``POST /api/transactions/import``.

    Always 200 unless validation aborted before any DB write happened (in
    which case the endpoint returns 400 + ErrorOut). In ``strict=true`` mode
    a single bad row rolls everything back: ``imported=0`` and the errors
    list explains why. In ``strict=false`` mode bad rows are skipped and
    listed but valid rows still commit.
    """

    mode: str                          # 'append' or 'replace'
    imported: int                      # rows successfully inserted
    skipped: int                       # rows skipped (dedup in append mode)
    errors: list[RowImportError]
    holdings_created: list[str]        # newly-auto-created holding tickers
    strict: bool                       # echoes the flag the caller used


# --------------------------------------------------------------------------- #
# Holdings + Groups (mutating)
# --------------------------------------------------------------------------- #
class HoldingCreate(Schema):
    """Body for ``POST /api/holdings``.

    Creates a Holding row. If ``initial_amount_eur`` is provided (and
    ``kind='portfolio'``), atomically logs the first Transaction too -- the
    Holding is rolled back if the price fetch fails.

    If ``group`` doesn't exist yet, a ``GroupConfig`` is auto-created with the
    optional ``target_weight`` / ``group_description``.
    """

    kind: str                            # 'portfolio' | 'watchlist'
    asset_class: str                     # 'stocks' | 'etfs'
    group: str                           # sector name; ignored for etfs (forced to 'ETFs')
    ticker: str
    name: str
    currency: str = "USD"
    # Optional config when creating a brand-new group.
    target_weight: Optional[float] = None
    group_description: Optional[str] = None
    # Optional initial buy (portfolio only).
    initial_amount_eur: Optional[float] = None
    initial_date: Optional[str] = None   # ISO YYYY-MM-DD; defaults to today
    initial_fee_eur: Optional[float] = None
    initial_note: Optional[str] = None


class HoldingOut(Schema):
    id: int
    kind: str
    asset_class: str
    group: str
    ticker: str
    name: str
    currency: str
    transaction: Optional[TransactionOut] = None   # filled if an initial buy was logged


class HoldingListItem(Schema):
    """Slim holding row for pickers (e.g. the swap from/to selectors). Unlike
    the portfolio snapshot this lists every holding -- including freshly-added
    coins that have no transactions yet, which a swap's *to* leg needs."""

    kind: str
    asset_class: str
    group: str
    ticker: str
    name: str
    currency: str


class GroupCreate(Schema):
    """Body for ``POST /api/groups``. Idempotent: get-or-create + update if exists."""

    asset_class: str
    name: str
    description: Optional[str] = None
    target_weight: Optional[float] = None


class GroupOut(Schema):
    asset_class: str
    name: str
    description: str
    target_weight: Optional[float] = None
    holdings_count: int = 0          # # of holdings using this group (any kind)


class GroupPatch(Schema):
    """Body for ``PATCH /api/groups/{name}``. Both fields optional; ``None``
    leaves the existing value alone, an empty string / null target_weight
    clears it."""

    description: Optional[str] = None
    target_weight: Optional[float] = None
    clear_target_weight: bool = False     # set true to wipe target_weight to NULL


# --------------------------------------------------------------------------- #
# Instruments (search / quote / history / indicators / score / estimate)
# --------------------------------------------------------------------------- #
class InstrumentSearchHit(Schema):
    symbol: str
    name: Optional[str] = None
    type: Optional[str] = None
    region: Optional[str] = None
    currency: Optional[str] = None


class InstrumentQuoteOut(Schema):
    symbol: str
    price: Optional[float] = None
    open: Optional[float] = None
    high: Optional[float] = None
    low: Optional[float] = None
    previous_close: Optional[float] = None
    change: Optional[float] = None
    change_percent: Optional[float] = None
    volume: Optional[float] = None
    latest_trading_day: Optional[str] = None


class HistoryFrameOut(Schema):
    """OHLCV history, columns as parallel arrays (one entry per date)."""

    symbol: str
    interval: str
    dates: list[str]
    open: list[Optional[float]]
    high: list[Optional[float]]
    low: list[Optional[float]]
    close: list[Optional[float]]
    volume: list[Optional[float]]


class IndicatorsOut(Schema):
    """``metrics.add_indicators`` output, returned split-orient."""

    symbol: str
    dates: list[str]
    columns: list[str]
    data: list[list[Optional[float]]]


class ScoreOut(Schema):
    ticker: str
    last_price: Optional[float] = None
    roc_20d_pct: Optional[float] = None
    rsi_14: Optional[float] = None
    zscore_20: Optional[float] = None
    trend: Optional[float] = None
    momentum: Optional[float] = None
    macd: Optional[float] = None
    mean_reversion: Optional[float] = None
    score: Optional[float] = None
    signal: Optional[str] = None


class EstimateSharesOut(Schema):
    ticker: str
    date: str
    amount_eur: float
    price_local: float
    listing_currency: str
    eur_per_local: float
    shares: float
    price_eur: float


class EstimateProceedsOut(Schema):
    """Mirror of EstimateSharesOut but in the units -> EUR direction. Powers
    the sell modal's live preview when the user enters share count instead
    of EUR amount."""

    ticker: str
    date: str
    shares: float
    price_local: float
    listing_currency: str
    eur_per_local: float
    amount_eur: float
    price_eur: float


class HoldingPositionOut(Schema):
    """Current net position for one ticker. Returned by
    ``GET /api/holdings/{ticker}/position`` so the sell modal can populate
    "Sell all" and the Stock page can hide its "+ Log sell" button when
    there's nothing to sell."""

    ticker: str
    shares: float
    cost_eur: float                 # cumulative cost basis on the held shares


# --------------------------------------------------------------------------- #
# Watchlist
# --------------------------------------------------------------------------- #
class WatchlistScoreOut(Schema):
    ticker: str
    name: str
    group: str
    currency: str
    status: str           # 'ok' | 'rate-limited' | 'no-data'
    recommendation: str   # 'BUY' | 'WATCH' | 'AVOID' | '-'
    last_price: Optional[float] = None
    roc_20d_pct: Optional[float] = None
    rsi_14: Optional[float] = None
    zscore_20: Optional[float] = None
    trend: Optional[float] = None
    momentum: Optional[float] = None
    macd: Optional[float] = None
    mean_reversion: Optional[float] = None
    score: Optional[float] = None
    signal: Optional[str] = None


class WatchlistOut(Schema):
    cached: bool
    items: list[WatchlistScoreOut]


# --------------------------------------------------------------------------- #
# Portfolio signals (same shape as watchlist scoring, applied to owned names).
# --------------------------------------------------------------------------- #
class PortfolioSignalsOut(Schema):
    cached: bool
    asset_class: str
    items: list[WatchlistScoreOut]


# --------------------------------------------------------------------------- #
# Diversification
# --------------------------------------------------------------------------- #
class DiversificationOut(Schema):
    metric: str           # 'pearson' | 'distance'
    asset_class: str
    lookback_days: int
    tickers: list[str]
    matrix: list[list[Optional[float]]]
    summary: dict[str, Any]
    cached: bool


# --------------------------------------------------------------------------- #
# Settings
# --------------------------------------------------------------------------- #
class SettingsOut(Schema):
    adapter: str
    base_currency: str
    av_quota: dict[str, Any]
    av_api_key_set: bool = False    # never returns the key itself


class SettingsUpdate(Schema):
    """Body for ``PUT /api/settings``. Both fields optional."""

    adapter: Optional[str] = None       # 'yfinance' or 'alphavantage'
    av_api_key: Optional[str] = None    # write-only; replaces stored key


class CacheClearedOut(Schema):
    """Response from ``POST /api/cache/clear``. Carries enough info for the
    SPA's top-bar Refresh button to show an "Updated at HH:MM" timestamp."""

    adapter: str
    at: str                              # ISO-8601 UTC timestamp


# --------------------------------------------------------------------------- #
# Auth
# --------------------------------------------------------------------------- #
class LoginIn(Schema):
    username: str
    password: str


class MeOut(Schema):
    authenticated: bool
    username: Optional[str] = None


# --------------------------------------------------------------------------- #
# Errors
# --------------------------------------------------------------------------- #
class ErrorOut(Schema):
    detail: str
    limited: Optional[bool] = None

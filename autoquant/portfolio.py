"""Portfolio bookkeeping: config, transactions ledger, valuation, history, diversification.

Design split:
  * **portfolio.yaml** holds the *structure* with two top-level sections,
    ``stocks`` (sector-grouped) and ``etfs`` (flat). Loaded here.
  * **data/transactions.csv** holds the *continuously changing* money flows
    (buys/sells with shares, price, FX). Loaded/edited here.

Everything is valued in the portfolio's base currency (EUR). Holdings can be
listed in any currency (USD/EUR/GBP); the ledger stores the price in the
*listing* currency plus the EUR-per-listing-currency FX rate at trade date, and
valuation walks the right per-currency FX series at runtime.

Market data and FX come from a pluggable
:class:`~autoquant.adapters.base.MarketDataAdapter` (Alpha Vantage or yfinance),
so these functions take an ``adapter`` argument.

Accounting convention: amounts and shares are *signed* -- a buy is positive
(cash in / shares added), a sell is negative. ``invested_eur`` is therefore net
cash deployed, which is exact for the buy-only case and a reasonable cash-flow
view once sells appear (no per-lot cost-basis tracking).
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable, Iterator, Optional

import numpy as np
import pandas as pd
import yaml

from .adapters.base import MarketDataAdapter
from .client import PROJECT_ROOT

DEFAULT_CONFIG_PATH = PROJECT_ROOT / "portfolio.yaml"
DEFAULT_LEDGER_PATH = PROJECT_ROOT / "data" / "transactions.csv"

# Group label used for ETF holdings (which are not sub-grouped in the YAML).
ETF_GROUP_LABEL = "ETFs"
CRYPTO_GROUP_LABEL = "Crypto"

TRANSACTION_COLUMNS = [
    "date",
    "group",
    "ticker",
    "action",
    "amount_eur",
    "price_local",        # close price in the listing currency on trade date
    "listing_currency",   # USD / EUR / GBP / ...
    "eur_per_local",      # EUR per 1 unit of listing currency on trade date
    "shares",
    "fee_eur",
    "note",
]


# --------------------------------------------------------------------------- #
# Config (portfolio.yaml / watchlist.yaml)
# --------------------------------------------------------------------------- #
def load_portfolio(path: str | Path = DEFAULT_CONFIG_PATH) -> dict:
    """Parse a portfolio/watchlist definition YAML into a dict.

    A bare or relative ``path`` that isn't found in the current directory falls
    back to the project root, so ``load_portfolio("watchlist.yaml")`` works
    regardless of where a notebook is launched from.
    """
    p = Path(path)
    if not p.is_file() and not p.is_absolute():
        fallback = PROJECT_ROOT / p
        if fallback.is_file():
            p = fallback
    with open(p) as fh:
        return yaml.safe_load(fh)


def _stocks_section(portfolio: dict) -> dict:
    """Return the ``stocks`` block, tolerating the legacy ``groups``-at-top layout."""
    if "stocks" in portfolio:
        return portfolio["stocks"] or {}
    if "groups" in portfolio:  # legacy: top-level groups == stocks
        return {"groups": portfolio["groups"]}
    return {}


def _etfs_section(portfolio: dict) -> dict:
    return portfolio.get("etfs") or {}


def _crypto_section(portfolio: dict) -> dict:
    """Crypto sleeve: same flat ``{holdings: [...]}`` shape as ETFs.

    A coin is identified by its Yahoo pair (e.g. ``BTC-EUR``, ``ETH-EUR``).
    The ledger prices every event in EUR; for native EUR pairs that means
    ``eur_per_local == 1.0`` and the FX leg collapses to a no-op."""
    return portfolio.get("crypto") or {}


def _iter_holdings(
    portfolio: dict,
    asset_class: str = "stocks",
) -> Iterator[tuple[str, dict]]:
    """Yield ``(group_label, holding_dict)`` pairs for the chosen asset class."""
    asset_class = asset_class.lower()
    if asset_class in ("stocks", "all"):
        groups = _stocks_section(portfolio).get("groups") or {}
        for group, gdef in groups.items():
            for h in (gdef or {}).get("holdings", []) or []:
                yield group, h
    if asset_class in ("etfs", "all"):
        for h in _etfs_section(portfolio).get("holdings", []) or []:
            yield ETF_GROUP_LABEL, h
    if asset_class in ("crypto", "all"):
        for h in _crypto_section(portfolio).get("holdings", []) or []:
            yield CRYPTO_GROUP_LABEL, h


def all_tickers(portfolio: dict, asset_class: str = "stocks") -> list[str]:
    """Flat list of every ticker (default: stocks only)."""
    return [h["ticker"] for _, h in _iter_holdings(portfolio, asset_class)]


def ticker_to_group(portfolio: dict, asset_class: str = "stocks") -> dict[str, str]:
    return {h["ticker"]: group for group, h in _iter_holdings(portfolio, asset_class)}


def ticker_to_name(portfolio: dict, asset_class: str = "stocks") -> dict[str, str]:
    return {h["ticker"]: h.get("name", h["ticker"]) for _, h in _iter_holdings(portfolio, asset_class)}


def ticker_to_currency(portfolio: dict, asset_class: str = "stocks") -> dict[str, str]:
    default_ccy = portfolio.get("quote_currency", "USD")
    return {h["ticker"]: h.get("currency", default_ccy) for _, h in _iter_holdings(portfolio, asset_class)}


def group_target_weights(portfolio: dict) -> dict[str, float]:
    """Target weights for stock groups (ETFs have none)."""
    groups = _stocks_section(portfolio).get("groups") or {}
    return {g: (gdef or {}).get("target_weight", float("nan")) for g, gdef in groups.items()}


# --------------------------------------------------------------------------- #
# Transactions ledger (data/transactions.csv)
# --------------------------------------------------------------------------- #
def empty_transactions() -> pd.DataFrame:
    """An empty ledger with the canonical columns."""
    return pd.DataFrame(columns=TRANSACTION_COLUMNS)


def _migrate_legacy(df: pd.DataFrame) -> pd.DataFrame:
    """Transparently upgrade a pre-multi-currency ledger (price_usd/eur_per_usd)."""
    if "price_local" in df.columns:
        return df
    if "price_usd" not in df.columns:
        return df  # nothing recognisable to migrate
    out = df.rename(columns={"price_usd": "price_local", "eur_per_usd": "eur_per_local"})
    if "listing_currency" not in out.columns:
        out["listing_currency"] = "USD"
    return out


def load_transactions(path: str | Path = DEFAULT_LEDGER_PATH) -> pd.DataFrame:
    """Load the transactions ledger (returns an empty frame if none exists).

    Auto-migrates the legacy ``price_usd/eur_per_usd`` schema by renaming columns
    and defaulting ``listing_currency`` to USD (since the legacy ledger was
    USD-only).
    """
    path = Path(path)
    if not path.is_file():
        return empty_transactions()
    df = pd.read_csv(path, parse_dates=["date"])
    df = _migrate_legacy(df)
    return df.sort_values("date").reset_index(drop=True)


def save_transactions(df: pd.DataFrame, path: str | Path = DEFAULT_LEDGER_PATH) -> None:
    """Persist the ledger, keeping the canonical column order."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    ordered = df.reindex(columns=TRANSACTION_COLUMNS)
    ordered = ordered.sort_values("date")
    ordered.to_csv(path, index=False)


# --------------------------------------------------------------------------- #
# FX helpers (multi-currency)
# --------------------------------------------------------------------------- #
def latest_eur_per_currency(adapter: MarketDataAdapter, currency: str) -> float:
    """Latest EUR per 1 unit of ``currency`` (returns 1.0 for EUR itself)."""
    return 1.0 if currency.upper() == "EUR" else float(adapter.get_fx_rate(currency, "EUR"))


def eur_per_currency_series(adapter: MarketDataAdapter, currency: str) -> Optional[pd.Series]:
    """Historical daily EUR per 1 unit of ``currency`` (``None`` for EUR)."""
    if currency.upper() == "EUR":
        return None
    return adapter.get_fx_daily(currency, "EUR")["close"]


def fx_rates_for(adapter: MarketDataAdapter, currencies: Iterable[str]) -> dict[str, float]:
    """Latest EUR-per-currency rates for a set of listing currencies."""
    return {c.upper(): latest_eur_per_currency(adapter, c) for c in set(currencies)}


def fx_series_for(adapter: MarketDataAdapter, currencies: Iterable[str]) -> dict[str, Optional[pd.Series]]:
    """Historical EUR-per-currency series for a set of listing currencies."""
    return {c.upper(): eur_per_currency_series(adapter, c) for c in set(currencies)}


# --------------------------------------------------------------------------- #
# Ledger mutation (estimate / add / record)
# --------------------------------------------------------------------------- #
def estimate_shares(
    adapter: MarketDataAdapter,
    ticker: str,
    date: str | pd.Timestamp,
    amount_eur: float,
    listing_currency: str = "USD",
) -> dict:
    """Estimate shares bought for ``amount_eur`` on ``date`` from EOD data.

    Uses the daily close on (or the last session before) ``date`` and the
    EUR-per-listing-currency rate on that date. Returns ``price_local``,
    ``listing_currency``, ``eur_per_local`` and the implied share count.
    """
    when = pd.Timestamp(date)
    close_local = float(adapter.get_daily(ticker)["close"].asof(when))
    if listing_currency.upper() == "EUR":
        rate = 1.0
    else:
        rate = float(adapter.get_fx_daily(listing_currency, "EUR")["close"].asof(when))
    price_eur = close_local * rate
    shares = amount_eur / price_eur
    return {
        "price_local": close_local,
        "listing_currency": listing_currency.upper(),
        "eur_per_local": rate,
        "shares": float(shares),
    }


def add_transaction(
    transactions: pd.DataFrame,
    *,
    date: str | pd.Timestamp,
    group: str,
    ticker: str,
    action: str,
    amount_eur: float,
    price_local: float,
    listing_currency: str,
    eur_per_local: float,
    shares: float,
    fee_eur: float = 0.0,
    note: str = "",
) -> pd.DataFrame:
    """Return a new ledger with one signed row appended (buy=+, sell=-)."""
    sign = -1.0 if action.lower() == "sell" else 1.0
    row = {
        "date": pd.Timestamp(date),
        "group": group,
        "ticker": ticker,
        "action": action.lower(),
        "amount_eur": sign * abs(amount_eur),
        "price_local": price_local,
        "listing_currency": listing_currency.upper(),
        "eur_per_local": eur_per_local,
        "shares": sign * abs(shares),
        "fee_eur": fee_eur,
        "note": note,
    }
    new = pd.concat([transactions, pd.DataFrame([row])], ignore_index=True)
    return new.sort_values("date").reset_index(drop=True)


def record_buy(
    adapter: MarketDataAdapter,
    *,
    date: str | pd.Timestamp,
    group: str,
    ticker: str,
    amount_eur: float,
    listing_currency: str = "USD",
    fee_eur: float = 0.0,
    note: str = "",
    transactions: Optional[pd.DataFrame] = None,
    path: str | Path = DEFAULT_LEDGER_PATH,
    save: bool = True,
) -> pd.DataFrame:
    """Log a EUR-denominated buy, estimating shares from market data on ``date``.

    Says "200 EUR of ARM on 2026-05-04" and fills in price, FX and shares for
    you. Pass ``listing_currency="EUR"`` (or GBP, ...) for non-USD listings.
    """
    if transactions is None:
        transactions = load_transactions(path)
    est = estimate_shares(adapter, ticker, date, amount_eur, listing_currency=listing_currency)
    updated = add_transaction(
        transactions,
        date=date,
        group=group,
        ticker=ticker,
        action="buy",
        amount_eur=amount_eur,
        price_local=est["price_local"],
        listing_currency=est["listing_currency"],
        eur_per_local=est["eur_per_local"],
        shares=est["shares"],
        fee_eur=fee_eur,
        note=note or "est. shares from EOD close",
    )
    if save:
        save_transactions(updated, path)
    return updated


# --------------------------------------------------------------------------- #
# Positions & valuation (point-in-time)
# --------------------------------------------------------------------------- #
def current_positions(
    transactions: pd.DataFrame,
    portfolio: Optional[dict] = None,
    asset_class: str = "all",
) -> pd.DataFrame:
    """Net shares and net invested EUR per ticker (signed sums of the ledger).

    If ``portfolio`` is given, ``asset_class`` filters which tickers are kept
    (``"stocks"``, ``"etfs"``, or ``"all"``) and group/name come from the config.
    """
    if transactions.empty:
        return pd.DataFrame(columns=["group", "name", "shares", "invested_eur"])

    agg = transactions.groupby("ticker").agg(
        shares=("shares", "sum"),
        invested_eur=("amount_eur", "sum"),
        fees_eur=("fee_eur", "sum"),
    )

    if portfolio is not None:
        keep = set(all_tickers(portfolio, asset_class)) if asset_class != "all" \
            else set(all_tickers(portfolio, "all"))
        agg = agg.loc[agg.index.intersection(keep)]
        groups = ticker_to_group(portfolio, asset_class="all")
        names = ticker_to_name(portfolio, asset_class="all")
        agg.insert(0, "group", [groups.get(t, "?") for t in agg.index])
        agg.insert(1, "name", [names.get(t, t) for t in agg.index])
    return agg


def latest_prices_local(adapter: MarketDataAdapter, tickers: Iterable[str]) -> pd.Series:
    """Latest available daily close in each ticker's listing currency."""
    prices = {t: float(adapter.get_daily(t)["close"].iloc[-1]) for t in tickers}
    return pd.Series(prices, name="price_local")


# Back-compat alias (legacy code referenced latest_prices_usd).
latest_prices_usd = latest_prices_local


def value_positions(
    positions: pd.DataFrame,
    prices_local: pd.Series,
    listing_currencies: dict[str, str] | pd.Series,
    fx_to_eur: dict[str, float],
) -> pd.DataFrame:
    """Add market value, P&L and weight (in EUR), respecting per-holding FX.

    ``listing_currencies`` maps ticker -> currency code; ``fx_to_eur`` maps
    currency code -> EUR per 1 unit. Use :func:`fx_rates_for` to build the latter.
    """
    out = positions.copy()
    if isinstance(listing_currencies, dict):
        listing_currencies = pd.Series(listing_currencies)
    out["price_local"] = prices_local.reindex(out.index)
    out["currency"] = listing_currencies.reindex(out.index).fillna("USD").str.upper()
    out["fx_to_eur"] = out["currency"].map(lambda c: fx_to_eur.get(c.upper(), float("nan")))
    out["price_eur"] = out["price_local"] * out["fx_to_eur"]
    out["value_eur"] = out["shares"] * out["price_eur"]
    out["cost_eur"] = out["invested_eur"]
    out["pnl_eur"] = out["value_eur"] - out["cost_eur"]
    out["return_pct"] = (out["value_eur"] / out["cost_eur"] - 1.0) * 100.0
    total = out["value_eur"].sum()
    out["weight"] = out["value_eur"] / total if total else float("nan")
    return out


def group_summary(
    valued: pd.DataFrame,
    portfolio: Optional[dict] = None,
) -> pd.DataFrame:
    """Aggregate a valued positions frame to the group level (with target drift)."""
    grouped = valued.groupby("group").agg(
        value_eur=("value_eur", "sum"),
        cost_eur=("cost_eur", "sum"),
        pnl_eur=("pnl_eur", "sum"),
    )
    grouped["return_pct"] = (grouped["value_eur"] / grouped["cost_eur"] - 1.0) * 100.0
    total = grouped["value_eur"].sum()
    grouped["weight"] = grouped["value_eur"] / total if total else float("nan")

    if portfolio is not None:
        targets = group_target_weights(portfolio)
        grouped["target_weight"] = [targets.get(g, float("nan")) for g in grouped.index]
        grouped["drift_pct"] = (grouped["weight"] - grouped["target_weight"]) * 100.0
    return grouped


def portfolio_totals(valued: pd.DataFrame) -> pd.Series:
    """Whole-portfolio totals (value, cost, P&L, return %)."""
    value = valued["value_eur"].sum()
    cost = valued["cost_eur"].sum()
    return pd.Series(
        {
            "value_eur": value,
            "cost_eur": cost,
            "pnl_eur": value - cost,
            "return_pct": (value / cost - 1.0) * 100.0 if cost else float("nan"),
        }
    )


# --------------------------------------------------------------------------- #
# Value history (time series, multi-currency)
# --------------------------------------------------------------------------- #
def price_history_local(adapter: MarketDataAdapter, tickers: Iterable[str]) -> pd.DataFrame:
    """Aligned daily close prices in each ticker's listing currency."""
    return adapter.get_close_prices(list(tickers), interval="daily")


# Back-compat alias
price_history_usd = price_history_local


def shares_timeline(
    transactions: pd.DataFrame,
    dates: pd.DatetimeIndex,
    tickers: Iterable[str],
) -> pd.DataFrame:
    """Shares held per ticker on each date in ``dates`` (step-forward holdings)."""
    tickers = list(tickers)
    holdings = pd.DataFrame(0.0, index=dates, columns=tickers)
    for ticker in tickers:
        rows = transactions[transactions["ticker"] == ticker]
        if rows.empty:
            continue
        cum = rows.groupby("date")["shares"].sum().cumsum()
        held = cum.reindex(cum.index.union(dates)).ffill().reindex(dates).fillna(0.0)
        holdings[ticker] = held
    return holdings


def value_history(
    adapter: MarketDataAdapter,
    transactions: pd.DataFrame,
    portfolio: dict,
    asset_class: str = "stocks",
) -> dict[str, pd.DataFrame | pd.Series]:
    """Build the EUR value history for an asset class (``stocks`` or ``etfs``).

    Returns a dict with:
      * ``by_ticker`` -- EUR value per ticker per day,
      * ``by_group``  -- EUR value per group per day,
      * ``total``     -- total EUR value per day,
      * ``invested``  -- cumulative net invested EUR per day (this asset class only).
    """
    tickers = all_tickers(portfolio, asset_class)
    if not tickers:
        empty_df = pd.DataFrame()
        empty_s = pd.Series(dtype=float)
        return {"by_ticker": empty_df, "by_group": empty_df, "total": empty_s, "invested": empty_s}

    prices_local = price_history_local(adapter, tickers)

    # Restrict to this asset class's own transaction window.
    asset_tx = transactions[transactions["ticker"].isin(tickers)]
    if not asset_tx.empty:
        start = asset_tx["date"].min()
        prices_local = prices_local.loc[prices_local.index >= start]

    # Fill NaN gaps from differing exchange calendars (US/Euronext/LSE/XETRA)
    # so a missing close on a non-trading day doesn't zero out the holding.
    prices_local = prices_local.ffill().bfill()

    # Per-ticker FX conversion to EUR.
    currencies = ticker_to_currency(portfolio, asset_class)
    fx_series = fx_series_for(adapter, currencies.values())
    prices_eur = pd.DataFrame(index=prices_local.index, columns=prices_local.columns, dtype=float)
    for ticker in prices_local.columns:
        ccy = currencies.get(ticker, "USD").upper()
        fx = fx_series.get(ccy)
        prices_eur[ticker] = prices_local[ticker] if fx is None \
            else adapter.to_eur(prices_local[ticker], fx)

    holdings = shares_timeline(asset_tx, prices_eur.index, tickers)
    by_ticker = holdings * prices_eur

    groups = ticker_to_group(portfolio, asset_class)
    by_group = by_ticker.T.groupby(by_ticker.columns.map(groups)).sum().T

    total = by_ticker.sum(axis=1).rename("total_value_eur")

    invested = (
        asset_tx.set_index("date")["amount_eur"].groupby(level=0).sum().cumsum()
    )
    invested = (
        invested.reindex(invested.index.union(by_ticker.index))
        .ffill()
        .reindex(by_ticker.index)
        .fillna(0.0)
        .rename("invested_eur")
    )

    return {
        "by_ticker": by_ticker,
        "by_group": by_group,
        "total": total,
        "invested": invested,
    }


# --------------------------------------------------------------------------- #
# Diversification: correlation analysis
# --------------------------------------------------------------------------- #
def correlation_matrix(
    adapter: MarketDataAdapter,
    portfolio: dict,
    asset_class: str = "stocks",
    lookback_days: int = 126,
) -> pd.DataFrame:
    """Daily-return correlation matrix for an asset class's holdings.

    Uses the most recent ``lookback_days`` trading days (default ~6 months)
    available in each ticker's history.
    """
    tickers = all_tickers(portfolio, asset_class)
    closes = adapter.get_close_prices(tickers, interval="daily")
    closes = closes.tail(lookback_days)
    returns = closes.pct_change().dropna(how="all")
    return returns.corr()


def diversification_summary(corr: pd.DataFrame) -> pd.Series:
    """Headline diversification numbers derived from a correlation matrix.

    ``avg_corr`` (off-diagonal mean) is the standard concentration gauge --
    lower is more diversified (0 = uncorrelated; 1 = a single name in disguise).
    """
    if corr.empty or len(corr) < 2:
        return pd.Series({"n_assets": len(corr), "avg_corr": float("nan")})

    arr = corr.to_numpy(copy=True)
    np.fill_diagonal(arr, np.nan)
    off = arr[~np.isnan(arr)]
    avg = float(np.mean(off))

    # Strongest / weakest pair (off-diagonal).
    iu = np.triu_indices_from(corr, k=1)
    pairs = pd.Series(corr.values[iu], index=[(corr.index[i], corr.columns[j]) for i, j in zip(*iu)])
    max_pair, max_val = pairs.idxmax(), pairs.max()
    min_pair, min_val = pairs.idxmin(), pairs.min()

    # Effective number of bets (simple: 1 / mean(corr_clipped))
    eff_n = 1.0 / max(abs(avg), 1e-6)

    return pd.Series({
        "n_assets": len(corr),
        "avg_corr": avg,
        "max_pair": f"{max_pair[0]}-{max_pair[1]}",
        "max_corr": float(max_val),
        "min_pair": f"{min_pair[0]}-{min_pair[1]}",
        "min_corr": float(min_val),
        "effective_n": float(eff_n),
    })


def _double_centered_distance(x: np.ndarray) -> np.ndarray:
    """Sample double-centered pairwise distance matrix for the 1-D vector ``x``."""
    a = np.abs(x[:, None] - x[None, :])
    return a - a.mean(axis=0, keepdims=True) - a.mean(axis=1, keepdims=True) + a.mean()


def distance_correlation_matrix(
    adapter: MarketDataAdapter,
    portfolio: dict,
    asset_class: str = "stocks",
    lookback_days: int = 126,
) -> pd.DataFrame:
    """Pairwise distance correlation of daily returns (catches non-linear ties).

    Distance correlation (Szekely & Rizzo, 2007) is in [0, 1]: it's 0 iff the two
    series are statistically independent, and 1 means perfect dependence (linear
    or non-linear). Pearson can return 0 while there is still strong non-linear
    dependence; distance correlation cannot. Pair-by-pair this is O(T^2) memory
    and time, so it's fine for a typical portfolio (~dozens of names, hundreds of
    days) but not for thousands of names.
    """
    tickers = all_tickers(portfolio, asset_class)
    closes = adapter.get_close_prices(tickers, interval="daily")
    closes = closes.tail(lookback_days)
    # Use a common observation window across all tickers (drop rows with any NaN
    # so each pair sees the same dates -- avoids artefacts from mixed exchange
    # calendars).
    returns = closes.pct_change().dropna(how="any")

    cols = list(returns.columns)
    if len(returns) < 3 or len(cols) < 2:
        return pd.DataFrame(index=cols, columns=cols, dtype=float)

    centered: dict[str, np.ndarray] = {}
    dvar2: dict[str, float] = {}
    for c in cols:
        A = _double_centered_distance(returns[c].to_numpy(dtype=float))
        centered[c] = A
        dvar2[c] = float((A * A).mean())

    mat = np.eye(len(cols), dtype=float)
    for i in range(len(cols)):
        Ai = centered[cols[i]]
        for j in range(i + 1, len(cols)):
            dcov2 = float((Ai * centered[cols[j]]).mean())
            denom = np.sqrt(dvar2[cols[i]] * dvar2[cols[j]])
            val = float(np.sqrt(max(dcov2, 0.0) / denom)) if denom > 0 else 0.0
            mat[i, j] = val
            mat[j, i] = val
    return pd.DataFrame(mat, index=cols, columns=cols)


def distance_diversification_summary(dcor: pd.DataFrame) -> pd.Series:
    """Headline diversification numbers from a distance-correlation matrix.

    Mirror of :func:`diversification_summary` for the [0, 1]-valued dCor: lower
    ``avg_dcor`` is more diversified, and ``effective_n`` is back-of-envelope
    "how many independent bets" you really have when *any* form of dependence is
    counted (not just linear).
    """
    if dcor.empty or len(dcor) < 2:
        return pd.Series({"n_assets": len(dcor), "avg_dcor": float("nan")})

    arr = dcor.to_numpy(copy=True)
    np.fill_diagonal(arr, np.nan)
    off = arr[~np.isnan(arr)]
    avg = float(np.mean(off))

    iu = np.triu_indices_from(dcor, k=1)
    pairs = pd.Series(dcor.values[iu], index=[(dcor.index[i], dcor.columns[j]) for i, j in zip(*iu)])
    max_pair, max_val = pairs.idxmax(), pairs.max()
    min_pair, min_val = pairs.idxmin(), pairs.min()

    eff_n = 1.0 / max(avg, 1e-6)

    return pd.Series({
        "n_assets": len(dcor),
        "avg_dcor": avg,
        "max_pair": f"{max_pair[0]}-{max_pair[1]}",
        "max_dcor": float(max_val),
        "min_pair": f"{min_pair[0]}-{min_pair[1]}",
        "min_dcor": float(min_val),
        "effective_n": float(eff_n),
    })

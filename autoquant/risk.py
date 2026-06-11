"""Portfolio-level risk & return analytics (Phase R1).

This module sits one level above ``metrics.py`` (which works on a single price
series). Here we work on a *matrix* of asset returns and a weight vector to
describe the risk of a whole portfolio:

  * covariance estimation -- sample and Ledoit-Wolf shrinkage,
  * the risk/return metric suite for the current allocation,
  * per-holding risk contributions + the effective number of bets.

Design choices (see docs/quant-research.md):
  * Returns are computed on **EUR-converted** daily closes so the covariance an
    EUR investor cares about includes the FX leg -- consistent with how
    ``portfolio.value_history`` values holdings.
  * The "portfolio return series" is the **current weights applied to historical
    asset returns** (a buy-and-hold counterfactual of today's allocation), not
    the realised account value -- the latter is contaminated by deposits /
    withdrawals and is not a clean return stream.

Pure-pandas/numpy/sklearn; no Django, notebook-usable, unit-testable.
"""

from __future__ import annotations

from typing import Iterable, Optional

import numpy as np
import pandas as pd
from sklearn.covariance import LedoitWolf

from . import metrics
from .adapters.base import MarketDataAdapter
from .portfolio import (
    all_tickers,
    fx_series_for,
    price_history_local,
    ticker_to_currency,
)

TRADING_DAYS = 252


# --------------------------------------------------------------------------- #
# Returns matrix (the shared input for covariance + portfolio returns)
# --------------------------------------------------------------------------- #
def eur_price_matrix(
    adapter: MarketDataAdapter,
    portfolio: dict,
    asset_class: str = "stocks",
    lookback_days: Optional[int] = None,
    full_history: bool = False,
) -> pd.DataFrame:
    """Aligned daily closes for the asset class, each converted to EUR.

    Mirrors the conversion ``portfolio.value_history`` does (per-ticker FX), but
    returns clean *prices* (no share/cashflow contamination) so the result is a
    valid input for return/covariance estimation. Optionally trims to the last
    ``lookback_days`` rows. ``full_history=True`` pulls the deep (max) window via
    ``adapter.get_close_history`` -- used by covariance/backtesting; the default
    uses the compact (~1y) window the rest of the app shares.
    """
    tickers = all_tickers(portfolio, asset_class)
    if not tickers:
        return pd.DataFrame()

    prices_local = (
        adapter.get_close_history(tickers)
        if full_history
        else price_history_local(adapter, tickers)
    ).ffill().bfill()
    currencies = ticker_to_currency(portfolio, asset_class)
    fx_series = fx_series_for(adapter, currencies.values())

    prices_eur = pd.DataFrame(
        index=prices_local.index, columns=prices_local.columns, dtype=float
    )
    for ticker in prices_local.columns:
        ccy = currencies.get(ticker, "USD").upper()
        fx = fx_series.get(ccy)
        prices_eur[ticker] = (
            prices_local[ticker] if fx is None else adapter.to_eur(prices_local[ticker], fx)
        )

    prices_eur = prices_eur.dropna(how="all")
    if lookback_days is not None and len(prices_eur) > lookback_days:
        prices_eur = prices_eur.iloc[-lookback_days:]
    return prices_eur


def returns_matrix(prices: pd.DataFrame) -> pd.DataFrame:
    """Daily simple returns from a price matrix (drops the first NaN row)."""
    return prices.pct_change().dropna(how="all")


def weighted_portfolio_returns(
    asset_returns: pd.DataFrame, weights: pd.Series
) -> pd.Series:
    """The daily return series of a *fixed-weight* portfolio.

    ``weights`` need not be normalised; they're renormalised over the tickers
    that actually have return data. This is the counterfactual "what would my
    current allocation have returned" stream that drives the risk metrics.
    """
    cols = [t for t in weights.index if t in asset_returns.columns]
    if not cols:
        return pd.Series(dtype=float)
    w = weights.reindex(cols).astype(float)
    total = w.sum()
    if total == 0:
        return pd.Series(dtype=float)
    w = w / total
    aligned = asset_returns[cols].fillna(0.0)
    return aligned.mul(w, axis=1).sum(axis=1)


def equity_curve(port_returns: pd.Series) -> pd.Series:
    """Growth of 1 unit from a return series (for drawdown / Calmar)."""
    clean = port_returns.dropna()
    if clean.empty:
        return pd.Series(dtype=float)
    return (1.0 + clean).cumprod()


# --------------------------------------------------------------------------- #
# Covariance estimation
# --------------------------------------------------------------------------- #
def sample_covariance(
    asset_returns: pd.DataFrame, periods_per_year: int = TRADING_DAYS
) -> pd.DataFrame:
    """Annualised sample covariance matrix (the noisy baseline)."""
    clean = asset_returns.dropna(how="any")
    cov = clean.cov(ddof=1) * periods_per_year
    return cov


def shrunk_covariance(
    asset_returns: pd.DataFrame, periods_per_year: int = TRADING_DAYS
) -> pd.DataFrame:
    """Annualised Ledoit-Wolf shrunk covariance matrix.

    Parameter-free shrinkage toward a structured target -- always
    well-conditioned and invertible, the robust default for small samples
    (see docs/quant-research.md §1.1). Falls back to the sample covariance if
    there are too few observations for the estimator.
    """
    clean = asset_returns.dropna(how="any")
    cols = list(clean.columns)
    if clean.shape[0] < 3 or clean.shape[1] < 2:
        return sample_covariance(asset_returns, periods_per_year)
    lw = LedoitWolf().fit(clean.values)
    cov = pd.DataFrame(lw.covariance_, index=cols, columns=cols) * periods_per_year
    return cov


def shrinkage_intensity(asset_returns: pd.DataFrame) -> float:
    """The Ledoit-Wolf shrinkage coefficient delta in [0,1] (0=sample, 1=target).

    Surfaced so the UI can show *how much* shrinkage was applied -- a high delta
    means the sample covariance was very noisy."""
    clean = asset_returns.dropna(how="any")
    if clean.shape[0] < 3 or clean.shape[1] < 2:
        return float("nan")
    return float(LedoitWolf().fit(clean.values).shrinkage_)


# --------------------------------------------------------------------------- #
# Metric suite for the current allocation
# --------------------------------------------------------------------------- #
def risk_metrics(
    port_returns: pd.Series,
    benchmark_returns: Optional[pd.Series] = None,
    risk_free: float = 0.0,
    cvar_level: float = 0.95,
    periods_per_year: int = TRADING_DAYS,
) -> dict:
    """Full risk/return metric suite for a portfolio return series.

    Returns sharpe, sortino, calmar, annualised return + volatility, max
    drawdown, historical VaR / CVaR, downside deviation, and (if a benchmark is
    given) beta. NaNs where a metric is undefined (e.g. empty series).
    """
    clean = port_returns.dropna()
    if clean.empty:
        return {k: float("nan") for k in (
            "ann_return", "ann_volatility", "sharpe", "sortino", "calmar",
            "max_drawdown", "var_95", "cvar_95", "downside_deviation", "beta",
        )}

    curve = equity_curve(clean)
    out = {
        "ann_return": metrics.annualized_return(curve, periods_per_year),
        "ann_volatility": metrics.annualized_volatility(clean, periods_per_year),
        "sharpe": metrics.sharpe_ratio(clean, risk_free, periods_per_year),
        "sortino": metrics.sortino_ratio(clean, risk_free, periods_per_year),
        "calmar": metrics.calmar_ratio(curve, periods_per_year),
        "max_drawdown": metrics.max_drawdown(curve),
        "var_95": metrics.value_at_risk(clean, cvar_level),
        "cvar_95": metrics.conditional_value_at_risk(clean, cvar_level),
        "downside_deviation": metrics.downside_deviation(clean, 0.0, periods_per_year),
        "beta": (
            metrics.beta(clean, benchmark_returns)
            if benchmark_returns is not None and not benchmark_returns.dropna().empty
            else float("nan")
        ),
    }
    return out


# --------------------------------------------------------------------------- #
# Risk contributions + effective bets
# --------------------------------------------------------------------------- #
def risk_contributions(weights: pd.Series, cov: pd.DataFrame) -> pd.DataFrame:
    """Marginal and percentage contribution of each holding to portfolio risk.

    Returns a frame indexed by ticker with columns:
      * ``weight``           -- the (renormalised) weight,
      * ``marginal``         -- marginal risk contribution (d sigma_p / d w_i),
      * ``contribution``     -- absolute risk contribution (w_i * marginal),
      * ``pct_contribution`` -- share of total portfolio volatility.

    Two weakly-correlated assets can have very different risk contributions if
    one has a much larger weight x vol -- this is the sharper diversification
    lens the correlation heatmap misses.
    """
    tickers = [t for t in weights.index if t in cov.columns]
    if not tickers:
        return pd.DataFrame(
            columns=["weight", "marginal", "contribution", "pct_contribution"]
        )
    w = weights.reindex(tickers).astype(float)
    if w.sum() != 0:
        w = w / w.sum()
    sigma = cov.loc[tickers, tickers].values
    wv = w.values
    port_var = float(wv @ sigma @ wv)
    port_vol = float(np.sqrt(port_var)) if port_var > 0 else float("nan")

    if not np.isfinite(port_vol) or port_vol == 0:
        marginal = np.full(len(tickers), np.nan)
        contrib = np.full(len(tickers), np.nan)
        pct = np.full(len(tickers), np.nan)
    else:
        marginal = (sigma @ wv) / port_vol           # d sigma_p / d w_i
        contrib = wv * marginal                       # absolute contribution
        pct = contrib / port_vol                      # fractions, sum to 1

    return pd.DataFrame(
        {
            "weight": wv,
            "marginal": marginal,
            "contribution": contrib,
            "pct_contribution": pct,
        },
        index=tickers,
    )


def effective_bets(weights: pd.Series, cov: pd.DataFrame) -> float:
    """Effective number of independent risk bets (diversification quality).

    Defined as 1 / sum(pct_contribution^2) -- the inverse Herfindahl of the risk
    contributions. Ranges from 1 (all risk in one holding) up to N (risk spread
    equally). A sharper "how diversified am I really" number than effective-N
    from average correlation.
    """
    rc = risk_contributions(weights, cov)
    pct = rc["pct_contribution"].dropna()
    if pct.empty:
        return float("nan")
    hhi = float((pct**2).sum())
    return float(1.0 / hhi) if hhi > 0 else float("nan")


def rolling_risk(
    port_returns: pd.Series, window: int = 21, periods_per_year: int = TRADING_DAYS
) -> dict[str, pd.Series]:
    """Rolling annualised volatility + the underwater (drawdown) curve, for charts."""
    clean = port_returns.dropna()
    if clean.empty:
        return {"volatility": pd.Series(dtype=float), "drawdown": pd.Series(dtype=float)}
    vol = metrics.rolling_volatility(clean, window, annualize=True, periods_per_year=periods_per_year)
    dd = metrics.drawdown(equity_curve(clean))
    return {"volatility": vol, "drawdown": dd}

"""Portfolio optimisation + actionable rebalancing (Phase R4).

Wraps PyPortfolioOpt on the Phase R1 Ledoit-Wolf shrunk covariance to turn the
risk model + factor views into *target weights*, then into a concrete buy/trim
plan vs. the current holdings. Advisory only -- nothing here ever executes.

Methods (see docs/quant-research.md §3):
  * **hrp**             Hierarchical Risk Parity -- the robust default; no
                        matrix inversion, no expected-returns estimate.
  * **min_variance**    global minimum-variance on the shrunk covariance.
  * **max_sharpe**      classic tangency portfolio (fragile -- offered as a
                        baseline, not a default).
  * **cvar**            minimise Conditional VaR (coherent tail-risk objective).
  * **black_litterman** market(-cap)-equilibrium prior updated with the R3
                        factor scores as views, then mean-variance on the
                        posterior -- the principled way to act on the signal.

Every optimiser is defensive: on a solver failure or a degenerate (<2 asset)
universe it falls back to equal weight, so a backtest or an API call never
crashes on one bad rebalance.
"""

from __future__ import annotations

from typing import Optional

import numpy as np
import pandas as pd

from . import risk

TRADING_DAYS = 252
METHODS = ("hrp", "min_variance", "max_sharpe", "cvar", "black_litterman")


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _equal_weight(cols) -> pd.Series:
    cols = list(cols)
    return pd.Series(1.0 / len(cols), index=cols) if cols else pd.Series(dtype=float)


def _to_series(weights: dict, cols) -> pd.Series:
    s = pd.Series({c: float(weights.get(c, 0.0)) for c in cols})
    s = s.clip(lower=0.0)
    total = s.sum()
    return s / total if total > 0 else _equal_weight(cols)


def _annualized_mu(returns: pd.DataFrame) -> pd.Series:
    return returns.mean() * TRADING_DAYS


def _apply_cap(weights: pd.Series, max_weight: float) -> pd.Series:
    """Cap weights at ``max_weight`` and redistribute the excess to the
    uncapped names, iterating to convergence.

    HRPOpt (unlike the EfficientFrontier methods) doesn't accept weight bounds,
    so we enforce the per-holding cap here. If the cap is infeasible
    (``max_weight * n < 1``) we fall back to equal weight."""
    if max_weight >= 1.0 or weights.empty:
        return weights
    n = len(weights)
    if max_weight * n < 1.0 - 1e-9:           # can't sum to 1 under the cap
        return _equal_weight(list(weights.index))
    w = weights.clip(lower=0.0).astype(float)
    if w.sum() > 0:
        w = w / w.sum()
    for _ in range(100):
        over = w > max_weight + 1e-12
        if not over.any():
            break
        excess = float((w[over] - max_weight).sum())
        w[over] = max_weight
        room = (max_weight - w[~over])
        room_total = float(room.sum())
        if room_total <= 0:
            break
        w[~over] = w[~over] + excess * room / room_total
    return w / w.sum()


def _clean_returns(returns: pd.DataFrame) -> pd.DataFrame:
    return returns.dropna(how="any")


# --------------------------------------------------------------------------- #
# Optimisers
# --------------------------------------------------------------------------- #
def optimize_hrp(returns: pd.DataFrame, max_weight: float = 1.0, **_) -> pd.Series:
    rr = _clean_returns(returns)
    cols = list(returns.columns)
    if rr.shape[1] < 2 or rr.shape[0] < 20:
        return _equal_weight(cols)
    try:
        from pypfopt import HRPOpt

        hrp = HRPOpt(rr)
        hrp.optimize()
        w = _to_series(hrp.clean_weights(), cols)
        # HRPOpt has no weight-bounds arg; enforce the cap ourselves.
        return _apply_cap(w, max_weight)
    except Exception:
        return _equal_weight(cols)


def optimize_min_variance(
    returns: pd.DataFrame, min_weight: float = 0.0, max_weight: float = 1.0, **_
) -> pd.Series:
    cols = list(returns.columns)
    if len(cols) < 2:
        return _equal_weight(cols)
    try:
        from pypfopt import EfficientFrontier

        cov = risk.shrunk_covariance(returns)
        mu = _annualized_mu(returns)
        ef = EfficientFrontier(mu, cov, weight_bounds=(min_weight, max_weight))
        ef.min_volatility()
        return _to_series(ef.clean_weights(), cols)
    except Exception:
        return optimize_hrp(returns)


def optimize_max_sharpe(
    returns: pd.DataFrame,
    min_weight: float = 0.0,
    max_weight: float = 1.0,
    risk_free: float = 0.0,
    **_,
) -> pd.Series:
    cols = list(returns.columns)
    if len(cols) < 2:
        return _equal_weight(cols)
    try:
        from pypfopt import EfficientFrontier

        cov = risk.shrunk_covariance(returns)
        mu = _annualized_mu(returns)
        ef = EfficientFrontier(mu, cov, weight_bounds=(min_weight, max_weight))
        try:
            ef.max_sharpe(risk_free_rate=risk_free)
        except Exception:
            ef.min_volatility()       # tangency can fail if no asset beats rf
        return _to_series(ef.clean_weights(), cols)
    except Exception:
        return optimize_hrp(returns)


def optimize_cvar(
    returns: pd.DataFrame,
    target_return: Optional[float] = None,
    min_weight: float = 0.0,
    max_weight: float = 1.0,
    **_,
) -> pd.Series:
    cols = list(returns.columns)
    rr = _clean_returns(returns)
    if len(cols) < 2 or rr.shape[0] < 30:
        return _equal_weight(cols)
    try:
        from pypfopt import EfficientCVaR

        mu = _annualized_mu(returns)
        ec = EfficientCVaR(mu, rr, weight_bounds=(min_weight, max_weight))
        if target_return is not None:
            ec.efficient_return(target_return)
        else:
            ec.min_cvar()
        return _to_series(ec.clean_weights(), cols)
    except Exception:
        return optimize_hrp(returns)


def optimize_black_litterman(
    returns: pd.DataFrame,
    views: Optional[dict] = None,
    market_caps: Optional[dict] = None,
    min_weight: float = 0.0,
    max_weight: float = 1.0,
    view_scale: float = 0.05,
    **_,
) -> pd.Series:
    """Black-Litterman: a market-equilibrium prior updated with ``views``.

    ``views`` is a per-ticker factor composite in [-1,1] (from R3); it's scaled
    to an absolute annual expected-return tilt (±``view_scale`` at the extremes).
    ``market_caps`` sets the prior; absent (the common case, since yfinance caps
    are unreliable) an *equal-cap* equilibrium prior is used.
    """
    cols = list(returns.columns)
    if len(cols) < 2:
        return _equal_weight(cols)
    try:
        from pypfopt import BlackLittermanModel, EfficientFrontier
        from pypfopt.black_litterman import market_implied_prior_returns

        cov = risk.shrunk_covariance(returns)
        caps = pd.Series({c: float((market_caps or {}).get(c, 1.0)) for c in cols})
        pi = market_implied_prior_returns(caps, 2.5, cov)

        absolute_views = None
        if views:
            absolute_views = {
                c: float(view_scale) * float(np.clip(views.get(c, 0.0), -1, 1))
                for c in cols
            }
        bl = BlackLittermanModel(cov, pi=pi, absolute_views=absolute_views)
        ret_bl = bl.bl_returns()
        cov_bl = bl.bl_cov()
        ef = EfficientFrontier(ret_bl, cov_bl, weight_bounds=(min_weight, max_weight))
        try:
            ef.max_sharpe()
        except Exception:
            ef.min_volatility()
        return _to_series(ef.clean_weights(), cols)
    except Exception:
        return optimize_hrp(returns)


_DISPATCH = {
    "hrp": optimize_hrp,
    "min_variance": optimize_min_variance,
    "max_sharpe": optimize_max_sharpe,
    "cvar": optimize_cvar,
    "black_litterman": optimize_black_litterman,
}


def optimize(method: str, returns: pd.DataFrame, **kwargs) -> pd.Series:
    """Dispatch to the named optimiser (falls back to HRP for unknown names)."""
    fn = _DISPATCH.get(method, optimize_hrp)
    return fn(returns, **kwargs)


# --------------------------------------------------------------------------- #
# Efficient frontier (for the chart)
# --------------------------------------------------------------------------- #
def efficient_frontier(returns: pd.DataFrame, n_points: int = 25) -> dict:
    """Sample the mean-variance efficient frontier: parallel (volatility, return)
    arrays plus the min-variance and max-Sharpe marker points. Best-effort."""
    cols = list(returns.columns)
    empty = {"volatility": [], "return": [], "min_var": None, "max_sharpe": None}
    if len(cols) < 2:
        return empty
    try:
        from pypfopt import EfficientFrontier

        cov = risk.shrunk_covariance(returns)
        mu = _annualized_mu(returns)

        # Return range between the min-var and max-return assets.
        ef_lo = EfficientFrontier(mu, cov)
        ef_lo.min_volatility()
        r_lo = ef_lo.portfolio_performance()[0]
        r_hi = float(mu.max())
        targets = np.linspace(r_lo, r_hi, n_points)

        vols, rets = [], []
        for t in targets:
            try:
                ef = EfficientFrontier(mu, cov)
                ef.efficient_return(float(t))
                r, v, _ = ef.portfolio_performance()
                rets.append(float(r))
                vols.append(float(v))
            except Exception:
                continue

        def _point(fn_name):
            try:
                ef = EfficientFrontier(mu, cov)
                getattr(ef, fn_name)()
                r, v, _ = ef.portfolio_performance()
                return {"volatility": float(v), "return": float(r)}
            except Exception:
                return None

        return {
            "volatility": vols,
            "return": rets,
            "min_var": _point("min_volatility"),
            "max_sharpe": _point("max_sharpe"),
        }
    except Exception:
        return empty


# --------------------------------------------------------------------------- #
# Rebalance plan (advisory)
# --------------------------------------------------------------------------- #
def rebalance_plan(
    current_weights: pd.Series,
    target_weights: pd.Series,
    portfolio_value: float,
    cost_bps: float = 10.0,
) -> dict:
    """Concrete buy/trim trades to move ``current`` -> ``target`` weights.

    Returns per-ticker {current_weight, target_weight, delta_weight, trade_eur
    (signed: + buy / - trim)} plus the one-way turnover and an estimated cost.
    Advisory only -- the user executes the trades themselves.
    """
    tickers = sorted(set(current_weights.index) | set(target_weights.index))
    cur = current_weights.reindex(tickers).fillna(0.0)
    tgt = target_weights.reindex(tickers).fillna(0.0)
    # Renormalise defensively.
    if cur.sum() > 0:
        cur = cur / cur.sum()
    if tgt.sum() > 0:
        tgt = tgt / tgt.sum()

    trades = []
    for t in tickers:
        dw = float(tgt[t] - cur[t])
        trades.append({
            "ticker": t,
            "current_weight": float(cur[t]),
            "target_weight": float(tgt[t]),
            "delta_weight": dw,
            "trade_eur": dw * float(portfolio_value),
        })
    trades.sort(key=lambda r: r["trade_eur"])      # trims first, buys last

    turnover = float((tgt - cur).abs().sum() / 2.0)   # one-way turnover
    est_cost_eur = turnover * (cost_bps / 1e4) * float(portfolio_value)
    return {
        "trades": trades,
        "turnover": turnover,
        "est_cost_eur": est_cost_eur,
    }

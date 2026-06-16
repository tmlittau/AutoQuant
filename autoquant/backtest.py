"""Walk-forward backtesting harness (Phase R2) -- the validation linchpin.

Everything downstream (the factor signals of R3, the optimizer of R4) only earns
its place by *beating the manual baseline here*, on an out-of-sample,
overfitting-corrected metric. So this module is built before them.

Design (see docs/quant-research.md §0):
  * **Walk-forward only.** Weights at a rebalance date use returns strictly
    *before* that date -- no in-sample peeking. A leak test in the unit suite
    shifts the price series and asserts past weights don't change.
  * **Costs.** Each rebalance pays ``cost_bps`` on its turnover (sum of absolute
    weight changes).
  * **Overfitting guard.** Every run reports the Probabilistic Sharpe Ratio and,
    when several strategies are compared in one batch, the Deflated Sharpe Ratio
    (Bailey & Lopez de Prado 2014) which corrects for selection across N trials.

Strategies are pluggable ``weight_fn(hist_returns, cols, ctx) -> pd.Series``
registered in ``STRATEGIES``. R2 ships the no-optimizer baselines
(buy-and-hold, equal-weight, inverse-vol / inverse-variance, manual target);
R3/R4 register factor-tilt and optimizer strategies into the same registry.
"""

from __future__ import annotations

from typing import Callable, Optional

import numpy as np
import pandas as pd
from scipy.stats import kurtosis, norm, skew

from . import risk

TRADING_DAYS = 252
_EULER_GAMMA = 0.5772156649015329

WeightFn = Callable[[pd.DataFrame, list, dict], pd.Series]


# --------------------------------------------------------------------------- #
# Strategy library (no-optimizer baselines; R3/R4 extend the registry)
# --------------------------------------------------------------------------- #
def _equal_weight(hist: pd.DataFrame, cols: list, ctx: dict) -> pd.Series:
    return pd.Series(1.0 / len(cols), index=cols)


def _inverse_vol(hist: pd.DataFrame, cols: list, ctx: dict) -> pd.Series:
    vol = hist[cols].std(ddof=1).replace(0, np.nan)
    inv = 1.0 / vol
    inv = inv.fillna(inv.mean() if inv.notna().any() else 1.0)
    return inv / inv.sum()


def _inverse_variance(hist: pd.DataFrame, cols: list, ctx: dict) -> pd.Series:
    var = hist[cols].var(ddof=1).replace(0, np.nan)
    inv = 1.0 / var
    inv = inv.fillna(inv.mean() if inv.notna().any() else 1.0)
    return inv / inv.sum()


def _manual_target(hist: pd.DataFrame, cols: list, ctx: dict) -> pd.Series:
    """Fixed user-supplied target weights (ctx['target_weights']); falls back to
    equal weight for any ticker not in the supplied map."""
    target = ctx.get("target_weights") or {}
    w = pd.Series({c: float(target.get(c, 0.0)) for c in cols})
    if w.sum() <= 0:
        return _equal_weight(hist, cols, ctx)
    return w / w.sum()


def _factor_tilt(hist: pd.DataFrame, cols: list, ctx: dict) -> pd.Series:
    """Tilt toward names with high cross-sectional factor composites (R3).

    Reconstructs a price proxy from the return history (factors are
    scale-invariant), computes the cross-sectional factor composite across the
    universe, and overweights the positive-composite names (long-only). Falls
    back to equal weight when no name scores positively (e.g. early in the
    window before 12-1 momentum has enough history)."""
    from . import factors

    if len(hist) < 30:
        return _equal_weight(hist, cols, ctx)
    prices = (1.0 + hist[cols]).cumprod()
    raw = {t: factors.compute_raw_factors(prices[t]) for t in cols}
    cs = factors.cross_sectional_scores(raw, regime_mult=ctx.get("regime_mult"))
    if cs.empty or "score" not in cs:
        return _equal_weight(hist, cols, ctx)
    pos = cs["score"].reindex(cols).fillna(0.0).clip(lower=0.0)
    if pos.sum() <= 0:
        return _equal_weight(hist, cols, ctx)
    return pos / pos.sum()


# key -> (weight_fn, default_rebalance, human label, needs-no-rebalance flag)
STRATEGIES: dict[str, dict] = {
    "buy_and_hold": {
        "fn": _equal_weight, "rebalance": "none",
        "label": "Buy & hold (equal weight, never rebalanced)",
    },
    "equal_weight": {
        "fn": _equal_weight, "rebalance": "M",
        "label": "Equal weight (rebalanced)",
    },
    "inverse_vol": {
        "fn": _inverse_vol, "rebalance": "M",
        "label": "Inverse volatility (risk-parity-lite)",
    },
    "inverse_variance": {
        "fn": _inverse_variance, "rebalance": "M",
        "label": "Inverse variance",
    },
    "manual_target": {
        "fn": _manual_target, "rebalance": "M",
        "label": "Manual target weights",
    },
    "factor_tilt": {
        "fn": _factor_tilt, "rebalance": "M",
        "label": "Factor tilt (momentum / trend / low-vol / reversal)",
    },
}


def _optimizer_strategy(method: str):
    """Build a backtest weight_fn that runs an R4 optimiser on the trailing
    return window at each rebalance (walk-forward)."""
    def _fn(hist: pd.DataFrame, cols: list, ctx: dict) -> pd.Series:
        from . import optimize

        if len(hist) < 60:
            return _equal_weight(hist, cols, ctx)
        w = optimize.optimize(method, hist[cols])
        return w.reindex(cols).fillna(0.0) if not w.empty else _equal_weight(hist, cols, ctx)
    return _fn


# Register the R4 optimisers as backtestable strategies (HRP / min-var / CVaR).
# Black-Litterman needs current factor views + caps, so it stays optimise-only.
for _key, _label in (
    ("hrp", "HRP (Hierarchical Risk Parity)"),
    ("min_variance", "Minimum variance (shrunk covariance)"),
    ("cvar", "Min-CVaR (tail-risk aware)"),
):
    STRATEGIES[_key] = {"fn": _optimizer_strategy(_key), "rebalance": "M", "label": _label}


def register_strategy(key: str, fn: WeightFn, *, rebalance: str = "M", label: str = "") -> None:
    """Register a strategy so the backtester + Strategy Lab pick it up.

    R3 (factor tilt) and R4 (HRP / CVaR / Black-Litterman) call this at import
    time so their strategies flow into the same engine without touching it."""
    STRATEGIES[key] = {"fn": fn, "rebalance": rebalance, "label": label or key}


# --------------------------------------------------------------------------- #
# Rebalance schedule
# --------------------------------------------------------------------------- #
def _rebalance_dates(
    dates: pd.DatetimeIndex, freq: str, warmup: int
) -> set:
    """The subset of ``dates`` (after a ``warmup`` of bars) on which to recompute
    weights. ``freq='none'`` rebalances once (buy & hold); else month/week/quarter
    ends."""
    if len(dates) <= warmup:
        return set()
    eligible = dates[warmup:]
    if freq == "none":
        return {eligible[0]}
    rule = {"W": "W", "M": "ME", "Q": "QE"}.get(freq, "ME")
    # Period-end markers within the eligible window.
    marks = pd.Series(eligible, index=eligible).resample(rule).last().dropna()
    out = set(marks.values)
    out.add(eligible[0])      # always set an initial allocation
    return out


# --------------------------------------------------------------------------- #
# Core engine
# --------------------------------------------------------------------------- #
def backtest(
    prices: pd.DataFrame,
    weight_fn: WeightFn,
    *,
    rebalance: str = "M",
    cost_bps: float = 10.0,
    warmup: int = 63,
    ctx: Optional[dict] = None,
) -> dict:
    """Run one strategy over ``prices`` (EUR price matrix, columns = tickers).

    Returns a dict with the daily strategy return series, the equity curve, the
    realised turnover, and the full risk metric suite. Walk-forward: the weights
    applied from a rebalance date onward are computed from returns *before* it.
    """
    ctx = ctx or {}
    cols_all = list(prices.columns)
    rets = prices.pct_change().dropna(how="all").fillna(0.0)
    dates = rets.index
    cost = cost_bps / 1e4

    if len(dates) <= warmup + 5:
        return _empty_result()

    rebal_on = _rebalance_dates(dates, rebalance, warmup)

    held: Optional[pd.Series] = None      # drifting actual weights
    equity = 1.0
    curve, strat_rets, turn_series = [], [], []
    total_turnover = 0.0

    for i, dt in enumerate(dates):
        # --- rebalance (walk-forward: use only data strictly before dt) ---
        if dt in rebal_on:
            hist = rets.iloc[:i]
            # tickers with enough non-zero history at this point
            cols = [c for c in cols_all if hist[c].abs().sum() > 0] or cols_all
            try:
                target = weight_fn(hist, cols, ctx)
            except Exception:
                target = pd.Series(1.0 / len(cols), index=cols)
            target = target.reindex(cols_all).fillna(0.0).clip(lower=0.0)
            if target.sum() > 0:
                target = target / target.sum()
                prev = held if held is not None else pd.Series(0.0, index=cols_all)
                turn = float((target - prev.reindex(cols_all).fillna(0.0)).abs().sum())
                equity *= (1.0 - cost * turn)
                total_turnover += turn
                turn_series.append(turn)
                held = target

        # --- apply today's return + drift the weights ---
        if held is not None:
            day_ret = float((held * rets.loc[dt]).sum())
            equity *= (1.0 + day_ret)
            strat_rets.append(day_ret)
            drifted = held * (1.0 + rets.loc[dt])
            s = drifted.sum()
            held = drifted / s if s > 0 else held
        else:
            strat_rets.append(0.0)
        curve.append(equity)

    strat_returns = pd.Series(strat_rets, index=dates)
    equity_curve = pd.Series(curve, index=dates)
    metrics = risk.risk_metrics(strat_returns)

    return {
        "returns": strat_returns,
        "equity_curve": equity_curve,
        "metrics": metrics,
        "turnover": total_turnover,
        "n_rebalances": len(turn_series),
        "psr": probabilistic_sharpe(strat_returns),
    }


def _empty_result() -> dict:
    return {
        "returns": pd.Series(dtype=float),
        "equity_curve": pd.Series(dtype=float),
        "metrics": {k: float("nan") for k in (
            "ann_return", "ann_volatility", "sharpe", "sortino", "calmar",
            "max_drawdown", "var_95", "cvar_95", "downside_deviation", "beta",
        )},
        "turnover": 0.0,
        "n_rebalances": 0,
        "psr": float("nan"),
    }


# --------------------------------------------------------------------------- #
# Overfitting guards: Probabilistic + Deflated Sharpe (Bailey & Lopez de Prado)
# --------------------------------------------------------------------------- #
def _per_obs_sharpe(returns: pd.Series) -> float:
    r = returns.dropna()
    sd = r.std(ddof=1)
    if len(r) < 4 or sd == 0 or np.isnan(sd):
        return float("nan")
    return float(r.mean() / sd)


def probabilistic_sharpe(returns: pd.Series, benchmark_sr: float = 0.0) -> float:
    """P(true Sharpe > ``benchmark_sr``) given track-record length + higher moments.

    ``benchmark_sr`` is a *per-observation* Sharpe threshold (0 = "is the Sharpe
    positive at all, accounting for skew/kurtosis and sample size"). Returns a
    probability in [0,1]; > 0.95 is the conventional "significant" bar.
    """
    r = returns.dropna()
    n = len(r)
    sr = _per_obs_sharpe(r)
    if np.isnan(sr) or n < 4:
        return float("nan")
    sk = float(skew(r))
    kt = float(kurtosis(r, fisher=False))   # Pearson (non-excess) kurtosis
    denom = np.sqrt(1.0 - sk * sr + ((kt - 1.0) / 4.0) * sr**2)
    if denom <= 0 or np.isnan(denom):
        return float("nan")
    z = (sr - benchmark_sr) * np.sqrt(n - 1) / denom
    return float(norm.cdf(z))


def expected_max_sharpe(trial_sr_std: float, n_trials: int) -> float:
    """Expected maximum *per-observation* Sharpe under the null across N
    independent trials -- the threshold the Deflated Sharpe deflates against."""
    if n_trials < 2 or trial_sr_std <= 0 or np.isnan(trial_sr_std):
        return 0.0
    a = norm.ppf(1.0 - 1.0 / n_trials)
    b = norm.ppf(1.0 - 1.0 / (n_trials * np.e))
    return float(trial_sr_std * ((1.0 - _EULER_GAMMA) * a + _EULER_GAMMA * b))


def deflated_sharpe(
    returns: pd.Series, n_trials: int, trial_sr_std: float
) -> float:
    """Deflated Sharpe Ratio: PSR against the expected-max-Sharpe under N trials.

    ``trial_sr_std`` is the std of the (per-observation) Sharpe ratios across the
    strategies/parameters that were tried -- the selection-bias correction. With
    ``n_trials < 2`` this collapses to ``probabilistic_sharpe`` (no deflation
    possible from a single trial).
    """
    sr0 = expected_max_sharpe(trial_sr_std, n_trials)
    return probabilistic_sharpe(returns, benchmark_sr=sr0)


# --------------------------------------------------------------------------- #
# Batch runner (computes DSR across the compared strategies)
# --------------------------------------------------------------------------- #
def run_strategies(
    prices: pd.DataFrame,
    specs: list[dict],
    *,
    cost_bps: float = 10.0,
    warmup: int = 63,
    ctx: Optional[dict] = None,
) -> dict:
    """Backtest several strategies on the same prices and cross-correct for
    multiple testing.

    Each spec: ``{"key": <registry key>, "rebalance": <override or None>}``.
    Returns per-strategy results plus the Deflated Sharpe of each, where the
    trial count is the number of strategies run and ``trial_sr_std`` is the
    cross-sectional std of their per-observation Sharpes.
    """
    ctx = ctx or {}
    results = []
    sr_list = []
    for spec in specs:
        key = spec["key"]
        meta = STRATEGIES.get(key)
        if meta is None:
            continue
        reb = spec.get("rebalance") or meta["rebalance"]
        res = backtest(
            prices, meta["fn"], rebalance=reb, cost_bps=cost_bps, warmup=warmup, ctx=ctx
        )
        res["key"] = key
        res["label"] = meta["label"]
        res["rebalance"] = reb
        results.append(res)
        sr_list.append(_per_obs_sharpe(res["returns"]))

    # Deflate each strategy's Sharpe against the family that was tried.
    valid_sr = [s for s in sr_list if not np.isnan(s)]
    n_trials = max(1, len(valid_sr))
    trial_std = float(np.std(valid_sr, ddof=1)) if len(valid_sr) >= 2 else 0.0
    for res in results:
        res["dsr"] = deflated_sharpe(res["returns"], n_trials, trial_std)

    return {"results": results, "n_trials": n_trials, "trial_sr_std": trial_std}

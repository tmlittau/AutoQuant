"""Unit tests for the Phase R4 optimisers + rebalance plan."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from autoquant import optimize


def _returns(n=400, k=5, seed=4):
    rng = np.random.default_rng(seed)
    factor = rng.normal(0.0004, 0.01, n)
    idx = pd.bdate_range("2022-01-03", periods=n)
    cols = {f"A{i}": (0.5 + 0.2 * i) * factor + rng.normal(0, 0.006, n) for i in range(k)}
    return pd.DataFrame(cols, index=idx)


# --------------------------------------------------------------------------- #
# Optimisers
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("method", ["hrp", "min_variance", "max_sharpe", "cvar"])
def test_optimizers_long_only_sum_to_one(method):
    w = optimize.optimize(method, _returns())
    assert len(w) == 5
    assert w.sum() == pytest.approx(1.0, abs=1e-6)
    assert (w >= -1e-9).all()                     # long-only


def test_black_litterman_with_factor_views():
    rets = _returns()
    views = {"A0": 1.0, "A4": -1.0}               # strong + / - factor views
    w = optimize.optimize("black_litterman", rets, views=views)
    assert w.sum() == pytest.approx(1.0, abs=1e-6)
    assert (w >= -1e-9).all()
    # The bullish view should not be the *least*-weighted name.
    assert w["A0"] >= w["A4"] - 1e-9


def test_max_weight_constraint_respected():
    w = optimize.optimize("min_variance", _returns(), max_weight=0.35)
    assert w.max() <= 0.35 + 1e-6


def test_hrp_respects_max_weight_via_cap():
    # HRPOpt itself ignores bounds; the post-cap must still bind.
    w = optimize.optimize("hrp", _returns(), max_weight=0.30)
    assert w.max() <= 0.30 + 1e-6
    assert w.sum() == pytest.approx(1.0, abs=1e-6)


def test_infeasible_cap_falls_back_to_equal():
    # 2 assets, cap 0.4 -> can't sum to 1 -> equal weight (0.5 each).
    w = optimize.optimize("hrp", _returns(k=2), max_weight=0.4)
    assert w.sum() == pytest.approx(1.0, abs=1e-6)
    assert w.iloc[0] == pytest.approx(0.5, abs=1e-6)


def test_degenerate_universe_falls_back_to_equal():
    one = _returns(k=1)
    w = optimize.optimize("min_variance", one)
    assert w.sum() == pytest.approx(1.0)
    assert len(w) == 1


def test_min_variance_lower_vol_than_equal_weight():
    from autoquant import risk

    rets = _returns()
    cov = risk.shrunk_covariance(rets)
    mv = optimize.optimize("min_variance", rets)
    ew = pd.Series(1 / rets.shape[1], index=rets.columns)

    def pvar(w):
        v = w.reindex(cov.columns).values
        return float(v @ cov.values @ v)

    assert pvar(mv) <= pvar(ew) + 1e-9            # min-var is, well, minimal


# --------------------------------------------------------------------------- #
# Efficient frontier
# --------------------------------------------------------------------------- #
def test_efficient_frontier_monotone_and_marked():
    fr = optimize.efficient_frontier(_returns(), n_points=15)
    assert len(fr["volatility"]) == len(fr["return"]) > 0
    # Higher target return generally needs higher volatility along the frontier.
    assert fr["volatility"][-1] >= fr["volatility"][0] - 1e-6
    assert fr["min_var"] is not None and fr["max_sharpe"] is not None


# --------------------------------------------------------------------------- #
# Rebalance plan
# --------------------------------------------------------------------------- #
def test_rebalance_plan_reconciles():
    cur = pd.Series({"A": 0.5, "B": 0.5})
    tgt = pd.Series({"A": 0.2, "B": 0.3, "C": 0.5})
    plan = optimize.rebalance_plan(cur, tgt, portfolio_value=10_000, cost_bps=10)
    by = {t["ticker"]: t for t in plan["trades"]}
    # A trimmed, C bought.
    assert by["A"]["trade_eur"] < 0
    assert by["C"]["trade_eur"] > 0
    # Trades sum to ~0 (a pure reallocation, no net cash).
    assert sum(t["trade_eur"] for t in plan["trades"]) == pytest.approx(0.0, abs=1e-6)
    # One-way turnover = half the summed absolute weight change = 0.5 here.
    assert plan["turnover"] == pytest.approx(0.5, abs=1e-9)
    assert plan["est_cost_eur"] > 0


def test_rebalance_plan_no_change_zero_turnover():
    w = pd.Series({"A": 0.6, "B": 0.4})
    plan = optimize.rebalance_plan(w, w, 10_000)
    assert plan["turnover"] == pytest.approx(0.0, abs=1e-9)
    assert plan["est_cost_eur"] == pytest.approx(0.0, abs=1e-9)

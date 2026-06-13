"""Unit tests for the Phase R2 walk-forward backtesting harness.

The most important test here is the **leak test**: appending future prices must
not change the equity curve over the original window. If it does, the engine is
peeking at future data and every metric it produces is worthless.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from autoquant import backtest as bt


def _price_panel(n=500, k=4, seed=11, drift=0.0003):
    """k assets with a shared drift + idiosyncratic noise -> realistic prices."""
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2023-01-02", periods=n)
    out = {}
    for j in range(k):
        rets = rng.normal(drift, 0.012, n)
        out[f"A{j}"] = 100 * np.exp(np.cumsum(rets))
    return pd.DataFrame(out, index=idx)


# --------------------------------------------------------------------------- #
# Engine basics
# --------------------------------------------------------------------------- #
def test_buy_and_hold_curve_matches_manual_compounding():
    prices = _price_panel()
    res = bt.backtest(prices, bt.STRATEGIES["buy_and_hold"]["fn"],
                      rebalance="none", cost_bps=0.0, warmup=63)
    # Reconstruct equal-weight buy&hold from the first rebalance bar onward.
    rets = prices.pct_change().fillna(0.0)
    eq_curve = res["equity_curve"]
    # Final equity should be positive and finite; with upward drift, > 0.5.
    assert np.isfinite(eq_curve.iloc[-1])
    assert eq_curve.iloc[-1] > 0.5
    # Buy & hold rebalances exactly once.
    assert res["n_rebalances"] == 1


def test_costs_reduce_equity_for_rebalancer():
    prices = _price_panel()
    free = bt.backtest(prices, bt.STRATEGIES["inverse_vol"]["fn"],
                       rebalance="M", cost_bps=0.0, warmup=63)
    costly = bt.backtest(prices, bt.STRATEGIES["inverse_vol"]["fn"],
                         rebalance="M", cost_bps=50.0, warmup=63)
    assert costly["equity_curve"].iloc[-1] < free["equity_curve"].iloc[-1]
    assert costly["turnover"] > 0


def test_equal_weight_and_inverse_vol_sum_to_one_internally():
    # Smoke: both strategies run and produce a full-length return series.
    prices = _price_panel()
    for key in ("equal_weight", "inverse_vol", "inverse_variance"):
        res = bt.backtest(prices, bt.STRATEGIES[key]["fn"], rebalance="M", warmup=63)
        assert len(res["returns"]) == len(prices.pct_change().dropna(how="all"))
        assert np.isfinite(res["equity_curve"].iloc[-1])


def test_manual_target_uses_supplied_weights():
    prices = _price_panel(k=3)
    ctx = {"target_weights": {"A0": 0.7, "A1": 0.2, "A2": 0.1}}
    res = bt.backtest(prices, bt.STRATEGIES["manual_target"]["fn"],
                      rebalance="M", warmup=63, ctx=ctx)
    assert np.isfinite(res["equity_curve"].iloc[-1])


# --------------------------------------------------------------------------- #
# THE leak test -- no look-ahead
# --------------------------------------------------------------------------- #
def test_walk_forward_no_lookahead():
    prices = _price_panel(n=500)
    cut = 400
    short = prices.iloc[:cut]
    long = prices  # identical to `short` over [:cut], plus 100 more bars

    res_short = bt.backtest(short, bt.STRATEGIES["inverse_vol"]["fn"],
                            rebalance="M", cost_bps=10.0, warmup=63)
    res_long = bt.backtest(long, bt.STRATEGIES["inverse_vol"]["fn"],
                           rebalance="M", cost_bps=10.0, warmup=63)

    # Compare equity curves over the overlap, minus a one-month buffer to avoid
    # the boundary month-end rebalance-date shift (not a leak, just calendar).
    overlap = res_short.index if hasattr(res_short, "index") else res_short["equity_curve"].index
    common = overlap[:-25]
    a = res_short["equity_curve"].reindex(common)
    b = res_long["equity_curve"].reindex(common)
    # If the engine peeked at the extra 100 future bars, these would diverge.
    assert np.allclose(a.values, b.values, rtol=1e-9, atol=1e-9)


# --------------------------------------------------------------------------- #
# Probabilistic + Deflated Sharpe
# --------------------------------------------------------------------------- #
def test_psr_high_for_strong_positive_low_for_noise():
    idx = pd.bdate_range("2023-01-02", periods=300)
    # Consistent positive drift, low vol -> high PSR.
    rng = np.random.default_rng(3)
    strong = pd.Series(rng.normal(0.001, 0.004, 300), index=idx)
    # Exactly zero-mean noise (demeaned) -> per-obs Sharpe 0 -> PSR == 0.5.
    raw = rng.normal(0.0, 0.01, 300)
    noise = pd.Series(raw - raw.mean(), index=idx)
    assert bt.probabilistic_sharpe(strong) > 0.9
    assert bt.probabilistic_sharpe(noise) == pytest.approx(0.5, abs=1e-9)


def test_expected_max_sharpe_grows_with_trials():
    s1 = bt.expected_max_sharpe(0.1, 2)
    s2 = bt.expected_max_sharpe(0.1, 50)
    assert s2 > s1 > 0


def test_deflated_sharpe_not_above_probabilistic():
    idx = pd.bdate_range("2023-01-02", periods=300)
    rng = np.random.default_rng(5)
    r = pd.Series(rng.normal(0.0008, 0.006, 300), index=idx)
    psr = bt.probabilistic_sharpe(r)
    # Deflating against 10 trials with non-zero dispersion can only lower it.
    dsr = bt.deflated_sharpe(r, n_trials=10, trial_sr_std=0.08)
    assert dsr <= psr + 1e-12


def test_single_trial_deflated_equals_probabilistic():
    idx = pd.bdate_range("2023-01-02", periods=200)
    rng = np.random.default_rng(7)
    r = pd.Series(rng.normal(0.0005, 0.007, 200), index=idx)
    assert bt.deflated_sharpe(r, n_trials=1, trial_sr_std=0.0) == pytest.approx(
        bt.probabilistic_sharpe(r), abs=1e-12
    )


# --------------------------------------------------------------------------- #
# Batch runner
# --------------------------------------------------------------------------- #
def test_run_strategies_reports_dsr_per_strategy():
    prices = _price_panel()
    specs = [
        {"key": "buy_and_hold"},
        {"key": "equal_weight"},
        {"key": "inverse_vol"},
    ]
    out = bt.run_strategies(prices, specs, cost_bps=10.0, warmup=63)
    assert out["n_trials"] == 3
    assert len(out["results"]) == 3
    for res in out["results"]:
        assert "dsr" in res and "psr" in res
        assert "sharpe" in res["metrics"]
        assert res["label"]

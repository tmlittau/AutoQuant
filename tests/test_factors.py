"""Unit tests for the Phase R3 price-only factor model + HMM regime layer."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from autoquant import factors, regime


def _trend_prices(n=400, daily=0.001, noise=0.002, seed=1):
    rng = np.random.default_rng(seed)
    rets = rng.normal(daily, noise, n)
    idx = pd.bdate_range("2023-01-02", periods=n)
    return pd.Series(100 * np.exp(np.cumsum(rets)), index=idx)


# --------------------------------------------------------------------------- #
# Raw + standalone factors
# --------------------------------------------------------------------------- #
def test_raw_factors_uptrend_is_bullish():
    prices = _trend_prices(daily=0.0015, noise=0.001)
    raw = factors.compute_raw_factors(prices)
    assert raw["momentum_12_1"] > 0          # rose over the year
    assert raw["trend_state"] == 1.0         # price > SMA50 > SMA200
    assert np.isfinite(raw["vol_ann"]) and raw["vol_ann"] > 0


def test_standalone_scores_bounded():
    prices = _trend_prices()
    scores = factors.standalone_scores(factors.compute_raw_factors(prices))
    assert set(scores.keys()) == set(factors.FACTOR_NAMES)
    for v in scores.values():
        assert -1.0 <= v <= 1.0


def test_composite_bounded_and_uptrend_positive():
    prices = _trend_prices(daily=0.0015, noise=0.001)
    res = factors.factor_score_series(prices)
    assert -1.0 <= res["score"] <= 1.0
    assert res["score"] > 0                  # a clean uptrend should lean BUY/HOLD+
    assert res["signal"] in ("BUY", "HOLD", "TRIM")


def test_short_history_degrades_gracefully():
    prices = _trend_prices(n=40)             # < 1y, no 12-1 momentum
    res = factors.factor_score_series(prices)
    assert np.isnan(res["momentum_12_1"])    # undefined
    assert np.isfinite(res["score"])         # but composite still computes


def test_regime_multiplier_renormalises_weights():
    base = factors._blended_weights(None, None)
    assert sum(base.values()) == pytest.approx(1.0)
    crisis = factors._blended_weights(None, regime.regime_factor_mult("crisis"))
    assert sum(crisis.values()) == pytest.approx(1.0)
    # Crisis lifts low_vol's share relative to momentum.
    assert crisis["low_vol"] > base["low_vol"]
    assert crisis["momentum"] < base["momentum"]


# --------------------------------------------------------------------------- #
# Cross-sectional
# --------------------------------------------------------------------------- #
def test_cross_sectional_scores_standardised():
    # Same noise path (fixed seed), vary only drift -> momentum is strictly
    # increasing in the drift, which isolates the cross-sectional ranking.
    universe = {
        f"T{i}": factors.compute_raw_factors(_trend_prices(daily=0.0004 * i, noise=0.001, seed=99))
        for i in range(1, 6)
    }
    out = factors.cross_sectional_scores(universe)
    assert len(out) == 5
    # Each z-column is ~mean-zero across the universe.
    for col in [c for c in out.columns if c.startswith("z_")]:
        assert abs(out[col].mean()) < 0.5
    assert "score" in out.columns and "signal" in out.columns
    # Highest-drift name has the highest momentum z-score.
    assert out["z_momentum"].idxmax() == "T5"
    assert out["z_momentum"].idxmin() == "T1"


def test_cross_sectional_empty():
    assert factors.cross_sectional_scores({}).empty


# --------------------------------------------------------------------------- #
# Regime detection
# --------------------------------------------------------------------------- #
def test_detect_regime_flags_high_vol_as_crisis():
    rng = np.random.default_rng(0)
    calm = rng.normal(0.0005, 0.004, 300)        # low vol
    crisis = rng.normal(-0.002, 0.03, 120)       # high vol, negative drift
    idx = pd.bdate_range("2023-01-02", periods=420)
    returns = pd.Series(np.concatenate([calm, crisis]), index=idx)
    res = regime.detect_regime(returns, n_states=2)
    # The series ends inside the high-vol block -> current regime is crisis.
    assert res["label"] == "crisis"
    assert 0.0 <= res["confidence"] <= 1.0
    assert len(res["states"]) == 2


def test_detect_regime_too_little_data_is_unknown():
    returns = pd.Series(np.random.default_rng(1).normal(0, 0.01, 30))
    res = regime.detect_regime(returns)
    assert res["label"] == "unknown"
    assert res["states"] == []


def test_regime_factor_mult_known_and_unknown():
    assert regime.regime_factor_mult("crisis")["low_vol"] > 1.0
    assert regime.regime_factor_mult("nonsense") == {}

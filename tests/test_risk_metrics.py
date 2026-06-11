"""Unit tests for the Phase R1 risk metrics + risk.py portfolio analytics.

Pure-library tests (no Django). Run from the repo root:

    .venv/bin/python -m pytest tests/ -q

Metrics are checked against hand-computed or analytically-known fixtures so a
regression in the math is caught immediately.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from autoquant import metrics, risk


# --------------------------------------------------------------------------- #
# Scalar metrics
# --------------------------------------------------------------------------- #
def test_downside_deviation_ignores_upside():
    # Up days contribute 0 to downside deviation; only -0.02 matters.
    r = pd.Series([0.02, 0.02, -0.02, 0.0])
    # sqrt(mean([0,0,0.0004,0])) * sqrt(252) = sqrt(0.0001) * sqrt(252)
    expected = np.sqrt(0.0004 / 4) * np.sqrt(252)
    assert metrics.downside_deviation(r) == pytest.approx(expected, rel=1e-9)


def test_sortino_higher_than_sharpe_for_upside_skew():
    # A series with big upside and small downside: Sortino should exceed Sharpe
    # because it doesn't penalise the upside dispersion.
    r = pd.Series([0.05, 0.05, 0.05, -0.01, 0.05, -0.01])
    assert metrics.sortino_ratio(r) > metrics.sharpe_ratio(r)


def test_value_at_risk_historical():
    # 100 returns from -0.10..-0.01 plus positives; 95% VaR is the 5th percentile
    # of the loss distribution (pandas linear interpolation lands near -0.05).
    losses = [-(i + 1) / 100 for i in range(10)]      # -0.01 .. -0.10
    gains = [0.01] * 90
    r = pd.Series(losses + gains)
    var = metrics.value_at_risk(r, level=0.95)
    assert var > 0
    assert 0.04 <= var <= 0.06


def test_cvar_is_at_least_var():
    # CVaR (mean of the tail) must be >= VaR (the threshold) for the same level.
    rng = np.random.default_rng(0)
    r = pd.Series(rng.normal(0, 0.01, 500))
    var = metrics.value_at_risk(r, 0.95)
    cvar = metrics.conditional_value_at_risk(r, 0.95)
    assert cvar >= var > 0


def test_beta_of_series_against_itself_is_one():
    rng = np.random.default_rng(1)
    bench = pd.Series(rng.normal(0, 0.01, 200))
    assert metrics.beta(bench, bench) == pytest.approx(1.0, rel=1e-9)
    # 2x the benchmark -> beta 2.
    assert metrics.beta(2 * bench, bench) == pytest.approx(2.0, rel=1e-9)


def test_calmar_positive_for_uptrend():
    # Steady climb with one small dip so there's a real (non-zero) drawdown
    # for the denominator; Calmar should be finite and positive.
    prices = pd.Series(np.linspace(100, 130, 300))
    prices.iloc[150] = prices.iloc[149] * 0.97       # a 3% blip
    calmar = metrics.calmar_ratio(prices)
    assert np.isfinite(calmar)
    assert calmar > 0


def test_calmar_nan_when_no_drawdown():
    # A monotonic series has zero drawdown -> Calmar undefined (guard returns nan).
    prices = pd.Series(np.linspace(100, 130, 300))
    assert np.isnan(metrics.calmar_ratio(prices))


def test_metrics_handle_empty_series():
    empty = pd.Series(dtype=float)
    assert np.isnan(metrics.sortino_ratio(empty))
    assert np.isnan(metrics.value_at_risk(empty))
    assert np.isnan(metrics.conditional_value_at_risk(empty))
    assert np.isnan(metrics.downside_deviation(empty))


# --------------------------------------------------------------------------- #
# Covariance estimation
# --------------------------------------------------------------------------- #
def _synthetic_returns(n=400, k=5, seed=7):
    """k correlated assets: a common factor plus idiosyncratic noise."""
    rng = np.random.default_rng(seed)
    factor = rng.normal(0, 0.01, n)
    cols = {}
    for i in range(k):
        beta = 0.5 + 0.2 * i
        cols[f"A{i}"] = beta * factor + rng.normal(0, 0.005, n)
    idx = pd.bdate_range("2024-01-01", periods=n)
    return pd.DataFrame(cols, index=idx)


def test_shrunk_covariance_is_psd_and_annualised():
    rets = _synthetic_returns()
    cov = risk.shrunk_covariance(rets)
    # Symmetric.
    assert np.allclose(cov.values, cov.values.T, atol=1e-12)
    # Positive semi-definite (all eigenvalues >= ~0).
    eig = np.linalg.eigvalsh(cov.values)
    assert eig.min() > -1e-10
    # Annualised: diagonal ~ daily var * 252, so vols are plausible (1%-ish daily
    # -> ~16% annual -> variance ~0.025). Just check order of magnitude > daily.
    sample_daily = rets.var(ddof=1).mean()
    assert cov.values.diagonal().mean() > sample_daily * 100


def test_shrinkage_intensity_in_unit_interval():
    rets = _synthetic_returns()
    delta = risk.shrinkage_intensity(rets)
    assert 0.0 <= delta <= 1.0


def test_shrunk_better_conditioned_than_sample_when_few_obs():
    # Few observations relative to assets -> sample cov nearly singular;
    # shrinkage must improve the condition number.
    rets = _synthetic_returns(n=8, k=5, seed=3)
    sample = risk.sample_covariance(rets)
    shrunk = risk.shrunk_covariance(rets)
    cond_sample = np.linalg.cond(sample.values)
    cond_shrunk = np.linalg.cond(shrunk.values)
    assert cond_shrunk < cond_sample


# --------------------------------------------------------------------------- #
# Portfolio returns + risk contributions
# --------------------------------------------------------------------------- #
def test_weighted_portfolio_returns_renormalises():
    rets = pd.DataFrame({"A": [0.01, 0.02], "B": [0.03, -0.01]})
    w = pd.Series({"A": 1.0, "B": 1.0})       # equal, un-normalised
    pr = risk.weighted_portfolio_returns(rets, w)
    assert pr.iloc[0] == pytest.approx(0.02)  # (0.01 + 0.03) / 2
    assert pr.iloc[1] == pytest.approx(0.005)


def test_risk_contributions_sum_to_one():
    rets = _synthetic_returns()
    cov = risk.shrunk_covariance(rets)
    w = pd.Series({c: 1.0 / rets.shape[1] for c in rets.columns})
    rc = risk.risk_contributions(w, cov)
    assert rc["pct_contribution"].sum() == pytest.approx(1.0, rel=1e-9)
    # Equal weights but unequal vols -> contributions are NOT all equal.
    assert rc["pct_contribution"].std() > 0


def test_effective_bets_between_one_and_n():
    rets = _synthetic_returns()
    cov = risk.shrunk_covariance(rets)
    w = pd.Series({c: 1.0 / rets.shape[1] for c in rets.columns})
    eb = risk.effective_bets(w, cov)
    assert 1.0 <= eb <= rets.shape[1] + 1e-9


def test_concentrated_portfolio_has_one_effective_bet():
    rets = _synthetic_returns()
    cov = risk.shrunk_covariance(rets)
    w = pd.Series({c: 0.0 for c in rets.columns})
    w.iloc[0] = 1.0                            # all in one name
    eb = risk.effective_bets(w, cov)
    assert eb == pytest.approx(1.0, abs=1e-6)


def test_risk_metrics_keys_present_and_finite():
    rets = _synthetic_returns()
    w = pd.Series({c: 1.0 / rets.shape[1] for c in rets.columns})
    pr = risk.weighted_portfolio_returns(rets, w)
    m = risk.risk_metrics(pr)
    for key in ("sharpe", "sortino", "calmar", "max_drawdown", "var_95", "cvar_95"):
        assert key in m
        assert np.isfinite(m[key])

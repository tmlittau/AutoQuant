"""Market-regime detection via a Gaussian Hidden Markov Model (Phase R3).

A 2-3 state HMM on (daily return, rolling volatility) features infers an
*unobservable* market state from observable price behaviour, returning the
current filtered regime + its probability (see docs/quant-research.md §2.3).

States are fit unlabelled and then named by their volatility ordering:
  * **calm**     -- lowest-volatility state (trend-friendly).
  * **volatile** -- middle state.
  * **crisis**   -- highest-volatility state (defensive).

The regime is a *conditioning layer*, not a predictor: ``regime_factor_mult``
maps the current label to per-factor weight multipliers that tilt the factor
blend (momentum down / low-vol + mean-reversion up in a crisis, the reverse in
calm). Everything is best-effort: too little data or an HMM fit failure yields
an ``unknown`` regime with neutral (empty) multipliers, so callers never crash.
"""

from __future__ import annotations

from typing import Optional

import numpy as np
import pandas as pd

TRADING_DAYS = 252

# Per-factor weight multipliers by regime (applied on top of the default blend).
# Calm: lean into trend/momentum. Crisis: cut momentum, lift the defensive /
# mean-reverting factors. Volatile: mild defensiveness.
REGIME_FACTOR_MULT: dict[str, dict[str, float]] = {
    "calm": {
        "momentum": 1.25, "trend_quality": 1.25, "low_vol": 0.80,
        "mean_reversion": 0.85, "short_reversal": 0.85,
    },
    "volatile": {
        "momentum": 1.0, "trend_quality": 1.0, "low_vol": 1.1,
        "mean_reversion": 1.05, "short_reversal": 1.05,
    },
    "crisis": {
        "momentum": 0.6, "trend_quality": 0.7, "low_vol": 1.4,
        "mean_reversion": 1.3, "short_reversal": 1.2,
    },
    "unknown": {},   # neutral -- falls back to the default blend
}

_LABELS_BY_NSTATES = {
    2: ["calm", "crisis"],
    3: ["calm", "volatile", "crisis"],
}


def regime_factor_mult(label: str) -> dict:
    """Per-factor weight multipliers for a regime label (empty = neutral)."""
    return dict(REGIME_FACTOR_MULT.get(label, {}))


def detect_regime(
    returns: pd.Series, n_states: int = 3, min_obs: int = 120
) -> dict:
    """Fit a Gaussian HMM on ``returns`` and report the current regime.

    Features are (return, rolling 10-day volatility). Returns a dict with the
    current ``label`` + ``confidence`` (filtered probability), per-state stats
    (mean return, annualised vol, current probability) ordered calm->crisis, and
    the number of observations. Degrades to ``{"label": "unknown", ...}`` when
    there isn't enough data or the fit fails.
    """
    clean = returns.dropna().astype(float)
    n_states = 3 if n_states not in (2, 3) else n_states

    unknown = {
        "label": "unknown",
        "confidence": float("nan"),
        "n_states": n_states,
        "n_obs": int(len(clean)),
        "states": [],
    }
    if len(clean) < min_obs:
        return unknown

    try:
        from hmmlearn.hmm import GaussianHMM

        vol = clean.rolling(10).std().bfill()
        feats = np.column_stack([clean.values, vol.values])
        # Standardise features so the two columns are comparably scaled.
        mu, sd = feats.mean(axis=0), feats.std(axis=0)
        sd[sd == 0] = 1.0
        x = (feats - mu) / sd

        model = GaussianHMM(
            n_components=n_states,
            covariance_type="diag",
            n_iter=200,
            random_state=42,
        )
        model.fit(x)
        hidden = model.predict(x)
        posteriors = model.predict_proba(x)

        # Recover each state's mean return + vol (un-standardised) to order them.
        stats = []
        for s in range(n_states):
            mask = hidden == s
            if not mask.any():
                stats.append({"state": s, "mean_ret": 0.0, "vol": np.inf})
                continue
            stats.append({
                "state": s,
                "mean_ret": float(clean.values[mask].mean()),
                "vol": float(clean.values[mask].std(ddof=0)),
            })
        # Order states by volatility -> assign calm..crisis labels.
        order = sorted(range(n_states), key=lambda s: stats[s]["vol"])
        labels = _LABELS_BY_NSTATES[n_states]
        state_to_label = {order[i]: labels[i] for i in range(n_states)}

        current_state = int(hidden[-1])
        current_probs = posteriors[-1]
        states_out = []
        for s in range(n_states):
            states_out.append({
                "label": state_to_label[s],
                "mean_return_daily": stats[s]["mean_ret"],
                "volatility_annual": float(stats[s]["vol"] * np.sqrt(TRADING_DAYS)),
                "probability": float(current_probs[s]),
            })
        states_out.sort(key=lambda d: d["volatility_annual"])

        return {
            "label": state_to_label[current_state],
            "confidence": float(current_probs[current_state]),
            "n_states": n_states,
            "n_obs": int(len(clean)),
            "states": states_out,
        }
    except Exception:
        return unknown

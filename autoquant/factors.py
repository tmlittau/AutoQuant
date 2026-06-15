"""Price-only cross-sectional factor model (Phase R3).

Replaces the ad-hoc four-indicator blend with a small set of academically
grounded, *price-derived* factors (see docs/quant-research.md §2). All are
computable from the daily closes we already fetch -- no fundamentals.

Factors (each a raw number, then normalised to [-1, 1]):
  * **momentum**       12-1 momentum: trailing 12-month return skipping the last
                       month (the canonical cross-sectional momentum factor; the
                       skip-month avoids short-term-reversal contamination).
  * **trend_quality**  price vs SMA50/SMA200 ordering + how consistently the
                       SMA50 has been rising (trend-following with a persistence
                       filter).
  * **low_vol**        the low-volatility anomaly: lower trailing realised vol
                       scores higher.
  * **mean_reversion** negated 20-day z-score (kept from the legacy engine): fade
                       stretched-high prices, buy stretched-low.
  * **short_reversal** negated trailing 1-month return: recent losers tend to
                       bounce.

Two usage modes:
  * **standalone** (one ticker, no peers): each factor self-normalised to
    [-1, 1] via a fixed transform. Powers the single-stock score view.
  * **cross-sectional** (a universe): each factor z-scored across the held
    tickers, which is the statistically sounder way to rank names against each
    other. Powers the portfolio signal scatter.

A regime layer (regime.py) can pass per-factor weight multipliers to tilt the
blend defensive/aggressive with the market state.
"""

from __future__ import annotations

from typing import Optional

import numpy as np
import pandas as pd

from . import metrics

TRADING_DAYS = 252

FACTOR_NAMES = ["momentum", "trend_quality", "low_vol", "mean_reversion", "short_reversal"]

# Default blend (sums to 1). Momentum + trend carry the most weight; the
# reversal/low-vol factors are diversifiers that cushion drawdowns.
DEFAULT_WEIGHTS = {
    "momentum": 0.30,
    "trend_quality": 0.25,
    "low_vol": 0.15,
    "mean_reversion": 0.15,
    "short_reversal": 0.15,
}

# Reuse the legacy stance thresholds on the [-1,1] composite for continuity.
BUY_THRESHOLD = 0.30
TRIM_THRESHOLD = -0.30


def _last(series: pd.Series) -> float:
    clean = series.dropna()
    return float(clean.iloc[-1]) if not clean.empty else float("nan")


# --------------------------------------------------------------------------- #
# Raw factor values (interpretable, per ticker)
# --------------------------------------------------------------------------- #
def compute_raw_factors(prices: pd.Series, benchmark: Optional[pd.Series] = None) -> dict:
    """Raw, human-readable factor values for one price series.

    ``benchmark`` is the benchmark's *daily return series* (not prices) -- it's
    used only for the (informational) beta and is aligned to the asset's returns
    on common dates.
    """
    close = prices.dropna().astype(float)
    n = len(close)

    # 12-1 momentum: return from ~12 months ago to ~1 month ago.
    if n > TRADING_DAYS:
        p_12m = float(close.iloc[-TRADING_DAYS])
        p_1m = float(close.iloc[-21])
        mom_12_1 = (p_1m / p_12m - 1.0) if p_12m > 0 else float("nan")
    else:
        mom_12_1 = float("nan")

    # Annualised trailing volatility (~6 months of daily returns).
    rets = close.pct_change().dropna()
    vol_ann = (
        float(rets.iloc[-126:].std(ddof=1) * np.sqrt(TRADING_DAYS))
        if len(rets) >= 20 else float("nan")
    )

    # Trend: ordering of price vs SMA50/SMA200, and SMA50 slope persistence.
    sma50 = metrics.sma(close, 50)
    sma200 = metrics.sma(close, 200)
    price = _last(close)
    s50, s200 = _last(sma50), _last(sma200)
    if np.isnan(s50) or np.isnan(s200):
        trend_state = 0.0
    elif price > s50 > s200:
        trend_state = 1.0
    elif price < s50 < s200:
        trend_state = -1.0
    else:
        trend_state = 0.0
    # Fraction of the last ~21 sessions the SMA50 rose (0..1).
    sma50_diff = sma50.diff().dropna().iloc[-21:]
    trend_slope = float((sma50_diff > 0).mean()) if not sma50_diff.empty else float("nan")

    # 20-day z-score (mean reversion) and trailing 1-month return (reversal).
    zscore = _last(metrics.rolling_zscore(close, 20))
    ret_1m = (price / float(close.iloc[-21]) - 1.0) if n > 21 and close.iloc[-21] > 0 else float("nan")

    # Beta vs benchmark (optional; informational). `benchmark` is already a
    # return series, so don't differentiate it again.
    beta = float("nan")
    if benchmark is not None and not benchmark.dropna().empty:
        beta = metrics.beta(rets, benchmark.dropna())

    return {
        "momentum_12_1": mom_12_1,
        "vol_ann": vol_ann,
        "trend_state": trend_state,
        "trend_slope": trend_slope,
        "zscore_20": zscore,
        "ret_1m": ret_1m,
        "beta": beta,
        "last_price": price,
    }


# --------------------------------------------------------------------------- #
# Standalone normalisation (one ticker -> each factor in [-1, 1])
# --------------------------------------------------------------------------- #
def _nan_to_zero(x: float) -> float:
    return 0.0 if (x is None or np.isnan(x)) else float(x)


def standalone_scores(raw: dict) -> dict:
    """Map raw factors to [-1, 1] without needing peers (single-ticker view)."""
    mom = raw.get("momentum_12_1", float("nan"))
    vol = raw.get("vol_ann", float("nan"))
    tstate = raw.get("trend_state", 0.0)
    tslope = raw.get("trend_slope", float("nan"))
    z = raw.get("zscore_20", float("nan"))
    r1m = raw.get("ret_1m", float("nan"))

    momentum = 0.0 if np.isnan(mom) else float(np.tanh(2.0 * mom))
    # Centre the low-vol score at 30% annualised vol; ±20% spans the range.
    low_vol = 0.0 if np.isnan(vol) else float(np.clip((0.30 - vol) / 0.20, -1, 1))
    slope_term = 0.0 if np.isnan(tslope) else (2.0 * tslope - 1.0)
    trend_quality = float(np.clip(0.6 * tstate + 0.4 * slope_term, -1, 1))
    mean_reversion = 0.0 if np.isnan(z) else float(np.clip(-z / 2.0, -1, 1))
    short_reversal = 0.0 if np.isnan(r1m) else float(np.tanh(-5.0 * r1m))

    return {
        "momentum": momentum,
        "trend_quality": trend_quality,
        "low_vol": low_vol,
        "mean_reversion": mean_reversion,
        "short_reversal": short_reversal,
    }


def _blended_weights(
    weights: Optional[dict], regime_mult: Optional[dict]
) -> dict:
    w = dict(weights or DEFAULT_WEIGHTS)
    if regime_mult:
        w = {k: w.get(k, 0.0) * float(regime_mult.get(k, 1.0)) for k in w}
    total = sum(w.values())
    if total <= 0:
        return {k: 1.0 / len(FACTOR_NAMES) for k in FACTOR_NAMES}
    return {k: v / total for k, v in w.items()}


def composite(
    scores: dict, weights: Optional[dict] = None, regime_mult: Optional[dict] = None
) -> float:
    """Weighted blend of normalised factor scores -> composite in [-1, 1]."""
    w = _blended_weights(weights, regime_mult)
    return float(sum(w.get(k, 0.0) * _nan_to_zero(scores.get(k, 0.0)) for k in w))


def stance(comp: float) -> str:
    if comp >= BUY_THRESHOLD:
        return "BUY"
    if comp <= TRIM_THRESHOLD:
        return "TRIM"
    return "HOLD"


def factor_score_series(
    prices: pd.Series,
    benchmark: Optional[pd.Series] = None,
    weights: Optional[dict] = None,
    regime_mult: Optional[dict] = None,
) -> dict:
    """Full standalone factor result for one ticker: raw values, normalised
    factor scores, the composite, and the BUY/HOLD/TRIM stance."""
    raw = compute_raw_factors(prices, benchmark)
    scores = standalone_scores(raw)
    comp = composite(scores, weights, regime_mult)
    return {
        "last_price": raw["last_price"],
        "momentum_12_1": raw["momentum_12_1"],
        "vol_ann": raw["vol_ann"],
        "zscore_20": raw["zscore_20"],
        "beta": raw["beta"],
        # normalised factor scores (the decomposition the UI shows)
        "f_momentum": scores["momentum"],
        "f_trend_quality": scores["trend_quality"],
        "f_low_vol": scores["low_vol"],
        "f_mean_reversion": scores["mean_reversion"],
        "f_short_reversal": scores["short_reversal"],
        "score": comp,
        "signal": stance(comp),
    }


# --------------------------------------------------------------------------- #
# Cross-sectional scoring (a universe -> z-scored factors + composite)
# --------------------------------------------------------------------------- #
def _zscore_clip(s: pd.Series, clip: float = 2.5) -> pd.Series:
    vals = s.astype(float)
    mu, sd = vals.mean(), vals.std(ddof=0)
    if sd == 0 or np.isnan(sd):
        return pd.Series(0.0, index=vals.index)
    return ((vals - mu) / sd).clip(-clip, clip)


def cross_sectional_scores(
    raw_by_ticker: dict[str, dict],
    weights: Optional[dict] = None,
    regime_mult: Optional[dict] = None,
) -> pd.DataFrame:
    """Cross-sectionally standardise factors across a universe of tickers.

    Each *raw* factor is converted to a peer-relative direction, z-scored across
    the universe (clipped at ±2.5), then blended into a composite z. The
    composite is mapped to BUY/HOLD/TRIM by its sign/magnitude (a name in the top
    of the cross-section is a relative BUY). Returns one row per ticker.
    """
    if not raw_by_ticker:
        return pd.DataFrame()

    # Build a directional raw frame (higher = more attractive) so the z-scores
    # share a sign convention.
    rows = {}
    for ticker, raw in raw_by_ticker.items():
        rows[ticker] = {
            "momentum": _nan_to_zero(raw.get("momentum_12_1")),
            "trend_quality": _nan_to_zero(raw.get("trend_state"))
            + 0.5 * _nan_to_zero(raw.get("trend_slope", 0.0)),
            "low_vol": -_nan_to_zero(raw.get("vol_ann")),       # low vol -> high score
            "mean_reversion": -_nan_to_zero(raw.get("zscore_20")),
            "short_reversal": -_nan_to_zero(raw.get("ret_1m")),
        }
    raw_frame = pd.DataFrame(rows).T

    z = pd.DataFrame({col: _zscore_clip(raw_frame[col]) for col in FACTOR_NAMES})
    w = _blended_weights(weights, regime_mult)
    composite_z = sum(w.get(k, 0.0) * z[k] for k in FACTOR_NAMES)

    out = z.copy()
    out.columns = [f"z_{c}" for c in out.columns]
    out["score"] = composite_z
    # Cross-sectional stance: top/bottom of the standardised composite.
    out["signal"] = out["score"].apply(
        lambda v: "BUY" if v >= 0.5 else ("TRIM" if v <= -0.5 else "HOLD")
    )
    return out

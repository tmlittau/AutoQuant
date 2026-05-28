"""Rule-based quant signals: turn metrics into a per-holding stance.

This is a *transparent, heuristic* scorer -- not financial advice and not a
trading bot. Each holding gets four sub-signals in [-1, +1] which are blended
into a single score, then mapped to BUY / HOLD / TRIM. The weights and
thresholds are deliberately simple so the output is easy to reason about and to
later wire into an automated workflow.

Sub-signals
-----------
* **trend**          price vs its 20/50-day SMAs (trend-following).
* **momentum**       RSI(14) recentred to [-1, 1] (RSI 50 -> 0).
* **macd**           sign of the MACD histogram.
* **mean_reversion** negated 20-day z-score -- stretched-high prices score
                     negative (fade), stretched-low score positive (buy the dip).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from . import metrics
from .adapters.base import MarketDataAdapter

DISCLAIMER = (
    "Heuristic signals for research only -- not financial advice. "
    "Review before acting; past performance does not predict future results."
)

WEIGHTS = {"trend": 0.35, "momentum": 0.30, "macd": 0.15, "mean_reversion": 0.20}
BUY_THRESHOLD = 0.35
TRIM_THRESHOLD = -0.35


def _last(series: pd.Series) -> float:
    clean = series.dropna()
    return float(clean.iloc[-1]) if not clean.empty else float("nan")


def score_series(prices: pd.Series) -> dict:
    """Compute the sub-signals, composite score and stance for one price series."""
    close = prices.dropna()
    price = _last(close)
    sma20 = _last(metrics.sma(close, 20))
    sma50 = _last(metrics.sma(close, 50))
    rsi_v = _last(metrics.rsi(close, 14))
    z_v = _last(metrics.rolling_zscore(close, 20))
    hist_v = _last(metrics.macd(close)["histogram"])
    roc_v = _last(metrics.momentum(close, 20))

    # Each sub-signal lives in [-1, +1].
    if np.isnan(sma20) or np.isnan(sma50):
        trend = 0.0
    elif price > sma20 > sma50:
        trend = 1.0
    elif price < sma20 < sma50:
        trend = -1.0
    else:
        trend = 0.0

    momentum_sig = 0.0 if np.isnan(rsi_v) else float(np.clip((rsi_v - 50) / 30.0, -1, 1))
    macd_sig = 0.0 if np.isnan(hist_v) else (1.0 if hist_v > 0 else -1.0)
    mean_rev = 0.0 if np.isnan(z_v) else float(np.clip(-z_v / 2.0, -1, 1))

    composite = (
        WEIGHTS["trend"] * trend
        + WEIGHTS["momentum"] * momentum_sig
        + WEIGHTS["macd"] * macd_sig
        + WEIGHTS["mean_reversion"] * mean_rev
    )

    if composite >= BUY_THRESHOLD:
        stance = "BUY"
    elif composite <= TRIM_THRESHOLD:
        stance = "TRIM"
    else:
        stance = "HOLD"

    return {
        "price_usd": price,
        "roc_20d_%": roc_v,
        "rsi_14": rsi_v,
        "zscore_20": z_v,
        "trend": trend,
        "momentum": momentum_sig,
        "macd": macd_sig,
        "mean_reversion": mean_rev,
        "score": composite,
        "signal": stance,
    }


def portfolio_signals(adapter: MarketDataAdapter, portfolio: dict) -> pd.DataFrame:
    """Score every holding in the portfolio; one row per ticker."""
    from .portfolio import all_tickers, ticker_to_group, ticker_to_name

    groups = ticker_to_group(portfolio)
    names = ticker_to_name(portfolio)

    rows = {}
    for ticker in all_tickers(portfolio):
        prices = adapter.get_daily(ticker)["close"]
        rows[ticker] = {"group": groups.get(ticker), "name": names.get(ticker), **score_series(prices)}

    df = pd.DataFrame(rows).T
    df.index.name = "ticker"
    numeric = [c for c in df.columns if c not in ("group", "name", "signal")]
    df[numeric] = df[numeric].apply(pd.to_numeric)
    return df.sort_values("score", ascending=False)


def recommend(score: float) -> str:
    """Map a composite score to a watchlist stance for a *non-owned* candidate."""
    if score >= BUY_THRESHOLD:
        return "BUY"
    if score <= TRIM_THRESHOLD:
        return "AVOID"
    return "WATCH"


def watchlist_signals(
    adapter: MarketDataAdapter,
    watchlist: dict,
    skip_errors: bool = True,
) -> pd.DataFrame:
    """Score every name in a watchlist and recommend BUY / WATCH / AVOID.

    Unlike :func:`portfolio_signals`, this is resilient: a ticker that can't be
    fetched (Alpha Vantage daily quota reached, or an unknown symbol on any
    provider) does not abort the run -- it gets a ``status`` of ``rate-limited``
    or ``no-data`` and is carried through with NaN metrics, so the notebook can
    show a clear coverage table and you can simply re-run later to fill the gaps.
    """
    from .adapters.base import DataUnavailableError
    from .client import AlphaVantageError, RateLimitError
    from .portfolio import all_tickers, ticker_to_currency, ticker_to_group, ticker_to_name

    groups = ticker_to_group(watchlist)
    names = ticker_to_name(watchlist)
    currencies = ticker_to_currency(watchlist)

    rows = {}
    for ticker in all_tickers(watchlist):
        base = {"group": groups.get(ticker), "name": names.get(ticker), "currency": currencies.get(ticker)}
        try:
            prices = adapter.get_daily(ticker)["close"]
            scored = score_series(prices)
            scored["last_price"] = scored.pop("price_usd")  # currency-neutral label
            rows[ticker] = {
                **base,
                **scored,
                "recommendation": recommend(scored["score"]),
                "status": "ok",
            }
        except RateLimitError:
            if not skip_errors:
                raise
            rows[ticker] = {**base, "recommendation": "-", "status": "rate-limited"}
        except (AlphaVantageError, DataUnavailableError, KeyError, ValueError):
            if not skip_errors:
                raise
            rows[ticker] = {**base, "recommendation": "-", "status": "no-data"}

    df = pd.DataFrame(rows).T
    df.index.name = "ticker"
    numeric = [c for c in df.columns if c not in ("group", "name", "currency", "signal", "recommendation", "status")]
    for col in numeric:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    if "score" in df.columns:
        df = df.sort_values("score", ascending=False, na_position="last")
    return df

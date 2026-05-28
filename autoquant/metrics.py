"""Technical and statistical metrics for price series.

Every function takes a price (or returns) ``pd.Series`` and returns a pandas
object aligned to the same index, so they compose cleanly and stay easy to plot.
Indicators are grouped loosely as: returns, moving averages, momentum,
mean-reversion, risk/volatility.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

TRADING_DAYS = 252


# --------------------------------------------------------------------------- #
# Returns
# --------------------------------------------------------------------------- #
def simple_returns(prices: pd.Series, periods: int = 1) -> pd.Series:
    """Arithmetic percentage return over ``periods`` (as a fraction, e.g. 0.01)."""
    return prices.pct_change(periods)


def log_returns(prices: pd.Series, periods: int = 1) -> pd.Series:
    """Continuously compounded (log) returns over ``periods``."""
    return np.log(prices / prices.shift(periods))


def cumulative_returns(prices: pd.Series) -> pd.Series:
    """Growth of 1 unit invested at the start (1.0 = break-even)."""
    return prices / prices.iloc[0]


# --------------------------------------------------------------------------- #
# Moving averages
# --------------------------------------------------------------------------- #
def sma(prices: pd.Series, window: int) -> pd.Series:
    """Simple moving average over ``window`` periods."""
    return prices.rolling(window).mean()


def ema(prices: pd.Series, span: int) -> pd.Series:
    """Exponential moving average with the given ``span``."""
    return prices.ewm(span=span, adjust=False).mean()


# --------------------------------------------------------------------------- #
# Momentum
# --------------------------------------------------------------------------- #
def momentum(prices: pd.Series, window: int = 10) -> pd.Series:
    """Rate of change: percent price move over ``window`` periods."""
    return prices.pct_change(window) * 100.0


def rsi(prices: pd.Series, window: int = 14) -> pd.Series:
    """Wilder's Relative Strength Index (0-100).

    Values above ~70 are conventionally "overbought", below ~30 "oversold".
    """
    delta = prices.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    avg_gain = gain.ewm(alpha=1 / window, adjust=False, min_periods=window).mean()
    avg_loss = loss.ewm(alpha=1 / window, adjust=False, min_periods=window).mean()
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def macd(
    prices: pd.Series,
    fast: int = 12,
    slow: int = 26,
    signal: int = 9,
) -> pd.DataFrame:
    """Moving Average Convergence Divergence.

    Returns a frame with ``macd`` (fast EMA - slow EMA), ``signal`` (EMA of the
    MACD line), and ``histogram`` (macd - signal).
    """
    macd_line = ema(prices, fast) - ema(prices, slow)
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    return pd.DataFrame(
        {
            "macd": macd_line,
            "signal": signal_line,
            "histogram": macd_line - signal_line,
        }
    )


# --------------------------------------------------------------------------- #
# Mean reversion
# --------------------------------------------------------------------------- #
def rolling_zscore(prices: pd.Series, window: int = 20) -> pd.Series:
    """Standardised distance of price from its rolling mean (in std-devs).

    A large positive z-score means the price is stretched above its recent mean
    (a mean-reversion short signal); a large negative one, stretched below.
    """
    mean = prices.rolling(window).mean()
    std = prices.rolling(window).std(ddof=0)
    return (prices - mean) / std


def bollinger_bands(
    prices: pd.Series,
    window: int = 20,
    num_std: float = 2.0,
) -> pd.DataFrame:
    """Bollinger Bands plus ``pct_b`` (position within the band, 0=lower, 1=upper)."""
    mid = prices.rolling(window).mean()
    std = prices.rolling(window).std(ddof=0)
    upper = mid + num_std * std
    lower = mid - num_std * std
    pct_b = (prices - lower) / (upper - lower)
    return pd.DataFrame(
        {"mid": mid, "upper": upper, "lower": lower, "pct_b": pct_b}
    )


def distance_from_sma(prices: pd.Series, window: int = 50) -> pd.Series:
    """Percent gap between price and its SMA (a simple mean-reversion gauge)."""
    moving = sma(prices, window)
    return (prices / moving - 1.0) * 100.0


# --------------------------------------------------------------------------- #
# Risk / volatility
# --------------------------------------------------------------------------- #
def rolling_volatility(
    returns: pd.Series,
    window: int = 21,
    annualize: bool = True,
    periods_per_year: int = TRADING_DAYS,
) -> pd.Series:
    """Rolling standard deviation of returns, optionally annualised."""
    vol = returns.rolling(window).std(ddof=1)
    if annualize:
        vol = vol * np.sqrt(periods_per_year)
    return vol


def drawdown(prices: pd.Series) -> pd.Series:
    """Fractional drop from the running peak at each point (<= 0)."""
    running_max = prices.cummax()
    return prices / running_max - 1.0


def max_drawdown(prices: pd.Series) -> float:
    """Worst peak-to-trough decline over the series (a negative fraction)."""
    return float(drawdown(prices).min())


def annualized_return(prices: pd.Series, periods_per_year: int = TRADING_DAYS) -> float:
    """Geometric annualised return implied by the first and last price."""
    total_growth = prices.iloc[-1] / prices.iloc[0]
    n_periods = len(prices) - 1
    if n_periods <= 0:
        return float("nan")
    return float(total_growth ** (periods_per_year / n_periods) - 1.0)


def annualized_volatility(returns: pd.Series, periods_per_year: int = TRADING_DAYS) -> float:
    """Annualised standard deviation of returns."""
    return float(returns.std(ddof=1) * np.sqrt(periods_per_year))


def sharpe_ratio(
    returns: pd.Series,
    risk_free: float = 0.0,
    periods_per_year: int = TRADING_DAYS,
) -> float:
    """Annualised Sharpe ratio; ``risk_free`` is an annual rate (e.g. 0.04)."""
    excess = returns - risk_free / periods_per_year
    std = returns.std(ddof=1)
    if std == 0 or np.isnan(std):
        return float("nan")
    return float(np.sqrt(periods_per_year) * excess.mean() / std)


# --------------------------------------------------------------------------- #
# Convenience: a standard indicator panel
# --------------------------------------------------------------------------- #
def add_indicators(df: pd.DataFrame, price_col: str = "close") -> pd.DataFrame:
    """Return a copy of ``df`` augmented with a standard set of indicators.

    Adds returns, SMAs (20/50/200), EMAs (12/26), 10-day momentum, RSI(14),
    MACD, 20-day z-score, Bollinger band width, and 21-day annualised volatility.
    """
    out = df.copy()
    price = out[price_col]

    out["ret"] = simple_returns(price)
    out["log_ret"] = log_returns(price)

    out["sma_20"] = sma(price, 20)
    out["sma_50"] = sma(price, 50)
    out["sma_200"] = sma(price, 200)
    out["ema_12"] = ema(price, 12)
    out["ema_26"] = ema(price, 26)

    out["momentum_10"] = momentum(price, 10)
    out["rsi_14"] = rsi(price, 14)

    macd_frame = macd(price)
    out["macd"] = macd_frame["macd"]
    out["macd_signal"] = macd_frame["signal"]
    out["macd_hist"] = macd_frame["histogram"]

    out["zscore_20"] = rolling_zscore(price, 20)
    bands = bollinger_bands(price, 20)
    out["bb_upper"] = bands["upper"]
    out["bb_lower"] = bands["lower"]
    out["bb_pct_b"] = bands["pct_b"]

    out["vol_21"] = rolling_volatility(out["ret"], 21)

    return out

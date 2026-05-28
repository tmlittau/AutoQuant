"""Currency conversion via Alpha Vantage FX endpoints.

Holdings are listed in USD but the portfolio is reported in EUR, so we need both
the latest USD->EUR rate and a historical daily series to value positions through
time. All endpoints used here are available on the free tier.

Convention used throughout: ``eur_per_usd`` = how many EUR one USD buys, so
``price_eur = price_usd * eur_per_usd``.
"""

from __future__ import annotations

import pandas as pd

from .client import AlphaVantageClient

_RATE_TTL = 10 * 60  # 10 minutes
_FX_DAILY_TTL = 24 * 60 * 60  # 1 day


def get_fx_rate(
    client: AlphaVantageClient,
    from_currency: str = "USD",
    to_currency: str = "EUR",
    force_refresh: bool = False,
) -> float:
    """Latest exchange rate: how many ``to_currency`` units per 1 ``from_currency``."""
    data = client.request(
        {
            "function": "CURRENCY_EXCHANGE_RATE",
            "from_currency": from_currency,
            "to_currency": to_currency,
        },
        cache_ttl=_RATE_TTL,
        force_refresh=force_refresh,
    )
    payload = data.get("Realtime Currency Exchange Rate") or {}
    rate = payload.get("5. Exchange Rate")
    if rate is None:
        raise KeyError(f"No exchange rate in response: keys={list(data)}")
    return float(rate)


def get_fx_daily(
    client: AlphaVantageClient,
    from_symbol: str = "USD",
    to_symbol: str = "EUR",
    outputsize: str = "compact",
    force_refresh: bool = False,
) -> pd.DataFrame:
    """Daily FX OHLC history for ``from_symbol``/``to_symbol`` (no volume)."""
    data = client.request(
        {
            "function": "FX_DAILY",
            "from_symbol": from_symbol,
            "to_symbol": to_symbol,
            "outputsize": outputsize,
        },
        cache_ttl=_FX_DAILY_TTL,
        force_refresh=force_refresh,
    )
    series_key = next((k for k in data if "Time Series" in k), None)
    if series_key is None:
        raise KeyError(f"No FX time series for {from_symbol}/{to_symbol}: keys={list(data)}")

    frame = pd.DataFrame.from_dict(data[series_key], orient="index")
    frame.columns = [col.split(". ", 1)[-1] for col in frame.columns]
    frame.index = pd.to_datetime(frame.index)
    frame = frame.sort_index().astype(float)
    frame.index.name = "date"
    return frame


def eur_per_usd_series(
    client: AlphaVantageClient,
    force_refresh: bool = False,
) -> pd.Series:
    """Historical daily USD->EUR close (EUR per 1 USD)."""
    fx = get_fx_daily(client, "USD", "EUR", force_refresh=force_refresh)
    series = fx["close"].rename("eur_per_usd")
    return series


def latest_eur_per_usd(client: AlphaVantageClient, force_refresh: bool = False) -> float:
    """Latest USD->EUR rate (EUR per 1 USD)."""
    return get_fx_rate(client, "USD", "EUR", force_refresh=force_refresh)


def to_eur(prices_usd: pd.Series, eur_per_usd: pd.Series) -> pd.Series:
    """Convert a USD price series to EUR using a daily FX series.

    The FX series is reindexed onto the price dates and forward-filled so that
    market holidays / weekend gaps between the two calendars don't create NaNs.
    """
    fx_aligned = eur_per_usd.reindex(prices_usd.index).ffill().bfill()
    return prices_usd * fx_aligned

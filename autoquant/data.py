"""High-level data access: fetch quotes and historical prices as pandas objects.

Notes on the Alpha Vantage *free* tier (relevant constraints baked into here):
  * ``TIME_SERIES_DAILY`` only allows ``outputsize="compact"`` (last ~100 days);
    ``full`` is a premium feature.
  * ``TIME_SERIES_WEEKLY`` and ``TIME_SERIES_MONTHLY`` return full history (20+
    years) for free -- use these for long-term analysis.
  * ``GLOBAL_QUOTE`` gives the latest price for free.
"""

from __future__ import annotations

from typing import Iterable, Optional

import pandas as pd

from .client import AlphaVantageClient

# Shorter cache lifetime for "current" quotes than for end-of-day history.
_QUOTE_TTL = 10 * 60  # 10 minutes
_HISTORY_TTL = 24 * 60 * 60  # 1 day

# interval -> Alpha Vantage function name and its time-series payload key.
_INTERVAL_FUNCTIONS = {
    "daily": "TIME_SERIES_DAILY",
    "weekly": "TIME_SERIES_WEEKLY",
    "monthly": "TIME_SERIES_MONTHLY",
}


def default_client(**kwargs) -> AlphaVantageClient:
    """Convenience constructor for a client wired to the project's .env key."""
    return AlphaVantageClient(**kwargs)


def _fetch_ohlcv(
    client: AlphaVantageClient,
    function: str,
    symbol: str,
    extra_params: Optional[dict] = None,
    force_refresh: bool = False,
) -> pd.DataFrame:
    """Fetch and tidy any of the OHLCV time-series endpoints into a DataFrame."""
    params = {"function": function, "symbol": symbol}
    if extra_params:
        params.update(extra_params)

    data = client.request(params, cache_ttl=_HISTORY_TTL, force_refresh=force_refresh)
    series_key = next((k for k in data if "Time Series" in k), None)
    if series_key is None:
        raise KeyError(f"No time-series payload for {symbol!r}: keys={list(data)}")

    frame = pd.DataFrame.from_dict(data[series_key], orient="index")
    # Columns arrive as "1. open", "2. high", ... -> strip the numeric prefix.
    frame.columns = [col.split(". ", 1)[-1] for col in frame.columns]
    frame.index = pd.to_datetime(frame.index)
    frame = frame.sort_index().astype(float)
    if "volume" in frame.columns:
        frame["volume"] = frame["volume"].astype("int64")
    frame.index.name = "date"
    frame.attrs["symbol"] = symbol
    return frame


def get_daily(
    client: AlphaVantageClient,
    symbol: str,
    outputsize: str = "compact",
    force_refresh: bool = False,
) -> pd.DataFrame:
    """Daily OHLCV history (free tier: ``outputsize="compact"`` = last ~100 rows).

    Columns: open, high, low, close, volume; indexed by a sorted DatetimeIndex.
    """
    return _fetch_ohlcv(
        client,
        "TIME_SERIES_DAILY",
        symbol,
        extra_params={"outputsize": outputsize},
        force_refresh=force_refresh,
    )


def get_weekly(
    client: AlphaVantageClient,
    symbol: str,
    force_refresh: bool = False,
) -> pd.DataFrame:
    """Weekly OHLCV history (free, full 20+ year history)."""
    return _fetch_ohlcv(client, "TIME_SERIES_WEEKLY", symbol, force_refresh=force_refresh)


def get_monthly(
    client: AlphaVantageClient,
    symbol: str,
    force_refresh: bool = False,
) -> pd.DataFrame:
    """Monthly OHLCV history (free, full 20+ year history)."""
    return _fetch_ohlcv(client, "TIME_SERIES_MONTHLY", symbol, force_refresh=force_refresh)


def get_history(
    client: AlphaVantageClient,
    symbol: str,
    interval: str = "daily",
    force_refresh: bool = False,
) -> pd.DataFrame:
    """Fetch OHLCV history for ``interval`` in {"daily", "weekly", "monthly"}."""
    if interval not in _INTERVAL_FUNCTIONS:
        raise ValueError(f"interval must be one of {sorted(_INTERVAL_FUNCTIONS)}, got {interval!r}")
    extra = {"outputsize": "compact"} if interval == "daily" else None
    return _fetch_ohlcv(
        client,
        _INTERVAL_FUNCTIONS[interval],
        symbol,
        extra_params=extra,
        force_refresh=force_refresh,
    )


def search_symbols(
    client: AlphaVantageClient,
    keywords: str,
    force_refresh: bool = False,
) -> pd.DataFrame:
    """Resolve a company name to Alpha Vantage tickers via SYMBOL_SEARCH.

    Returns the candidate matches with their region and listing currency, which
    is the reliable way to find the exact symbol for non-US listings (e.g. the
    correct suffix for London/Euronext stocks).
    """
    payload = client.request(
        {"function": "SYMBOL_SEARCH", "keywords": keywords},
        cache_ttl=_HISTORY_TTL,
        force_refresh=force_refresh,
    )
    matches = payload.get("bestMatches") or []
    rows = [{k.split(". ", 1)[-1]: v for k, v in match.items()} for match in matches]
    return pd.DataFrame(rows)


def get_quote(
    client: AlphaVantageClient,
    symbol: str,
    force_refresh: bool = False,
) -> pd.Series:
    """Latest quote for ``symbol`` as a tidy Series (price, change, volume, ...)."""
    data = client.request(
        {"function": "GLOBAL_QUOTE", "symbol": symbol},
        cache_ttl=_QUOTE_TTL,
        force_refresh=force_refresh,
    )
    quote = data.get("Global Quote") or {}
    if not quote:
        raise KeyError(f"No quote payload for {symbol!r}: keys={list(data)}")

    # Keys look like "05. price"; strip the numeric prefix for readability.
    cleaned = {key.split(". ", 1)[-1]: value for key, value in quote.items()}

    numeric_fields = ["open", "high", "low", "price", "volume", "previous close", "change"]
    for field in numeric_fields:
        if field in cleaned:
            cleaned[field] = pd.to_numeric(cleaned[field], errors="coerce")
    if "change percent" in cleaned:
        cleaned["change percent"] = pd.to_numeric(
            str(cleaned["change percent"]).rstrip("%"), errors="coerce"
        )

    return pd.Series(cleaned, name=symbol)


def get_quotes(
    client: AlphaVantageClient,
    symbols: Iterable[str],
    force_refresh: bool = False,
) -> pd.DataFrame:
    """Latest quotes for several symbols, one row per symbol."""
    rows = [get_quote(client, sym, force_refresh=force_refresh) for sym in symbols]
    frame = pd.DataFrame(rows)
    frame.index.name = "symbol"
    return frame


def get_close_prices(
    client: AlphaVantageClient,
    symbols: Iterable[str],
    interval: str = "daily",
    force_refresh: bool = False,
) -> pd.DataFrame:
    """Aligned closing prices for several symbols (one column each).

    Rows are dates (outer-joined, sorted); columns are symbols.
    """
    closes = {
        sym: get_history(client, sym, interval=interval, force_refresh=force_refresh)["close"]
        for sym in symbols
    }
    frame = pd.DataFrame(closes).sort_index()
    frame.index.name = "date"
    return frame

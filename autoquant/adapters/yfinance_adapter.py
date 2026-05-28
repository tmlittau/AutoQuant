"""yfinance adapter -- free, unlimited market data from Yahoo Finance.

Notable differences from Alpha Vantage handled here:
  * **Symbols**: Yahoo uses different exchange suffixes than Alpha Vantage, so a
    canonical ``TSCO.LON`` is mapped to ``TSCO.L`` (see ``_AV_TO_YF_SUFFIX``).
    Provide ``symbol_map`` for any one-off overrides.
  * **Adjusted prices**: ``auto_adjust=True`` (default) returns split/dividend
    adjusted OHLC -- preferable for return-based signals.
  * **No quota**: there is no daily request cap, so an in-memory cache is enough.
"""

from __future__ import annotations

import logging
from typing import Optional

import pandas as pd

from .base import DataUnavailableError, MarketDataAdapter

logging.getLogger("yfinance").setLevel(logging.CRITICAL)

# Canonical (Alpha-Vantage-style) suffix -> Yahoo Finance suffix.
_AV_TO_YF_SUFFIX = {
    ".LON": ".L",    # London
    ".PAR": ".PA",   # Euronext Paris
    ".AMS": ".AS",   # Euronext Amsterdam
    ".DEX": ".DE",   # XETRA
    ".FRK": ".F",    # Frankfurt
}


class YFinanceAdapter(MarketDataAdapter):
    """Market data via the ``yfinance`` package (Yahoo Finance)."""

    name = "yfinance"

    def __init__(self, symbol_map: Optional[dict[str, str]] = None, auto_adjust: bool = True) -> None:
        import yfinance as yf  # imported lazily so the package is optional

        self._yf = yf
        self.symbol_map = dict(symbol_map or {})
        self.auto_adjust = auto_adjust
        self._cache: dict[tuple, pd.DataFrame] = {}

    # ------------------------------------------------------------------ #
    # Symbol handling
    # ------------------------------------------------------------------ #
    def resolve(self, symbol: str) -> str:
        if symbol in self.symbol_map:
            return self.symbol_map[symbol]
        for av_suffix, yf_suffix in _AV_TO_YF_SUFFIX.items():
            if symbol.endswith(av_suffix):
                return symbol[: -len(av_suffix)] + yf_suffix
        return symbol

    # ------------------------------------------------------------------ #
    # Fetch + tidy
    # ------------------------------------------------------------------ #
    def _history(self, symbol: str, period: str, interval: str) -> pd.DataFrame:
        key = (symbol, period, interval, self.auto_adjust)
        if key in self._cache:
            return self._cache[key]

        yf_symbol = self.resolve(symbol)
        raw = self._yf.Ticker(yf_symbol).history(
            period=period, interval=interval, auto_adjust=self.auto_adjust
        )
        if raw is None or raw.empty:
            raise DataUnavailableError(
                f"yfinance returned no data for {symbol!r} (resolved to {yf_symbol!r})"
            )
        tidy = self._tidy(raw, symbol)
        self._cache[key] = tidy
        return tidy

    @staticmethod
    def _tidy(raw: pd.DataFrame, symbol: str) -> pd.DataFrame:
        frame = raw.rename(columns=str.lower)[["open", "high", "low", "close", "volume"]].copy()
        idx = pd.to_datetime(frame.index)
        if idx.tz is not None:
            idx = idx.tz_localize(None)
        frame.index = idx.normalize()
        frame = frame.sort_index().astype(float)
        # Yahoo sometimes includes the current day with NaN OHLC for non-US
        # exchanges before the local close has settled. Trim trailing NaN-close
        # rows so latest_prices / iloc[-1] always sees a real datapoint.
        last_valid = frame["close"].last_valid_index()
        if last_valid is not None and last_valid != frame.index[-1]:
            frame = frame.loc[:last_valid]
        frame["volume"] = frame["volume"].fillna(0).astype("int64")
        frame.index.name = "date"
        frame.attrs["symbol"] = symbol
        return frame

    # ------------------------------------------------------------------ #
    # Primitives
    # ------------------------------------------------------------------ #
    def get_daily(self, symbol: str, outputsize: str = "compact") -> pd.DataFrame:
        period = "1y" if outputsize == "compact" else "max"
        return self._history(symbol, period, "1d")

    def get_weekly(self, symbol: str) -> pd.DataFrame:
        return self._history(symbol, "max", "1wk")

    def get_monthly(self, symbol: str) -> pd.DataFrame:
        return self._history(symbol, "max", "1mo")

    def get_quote(self, symbol: str) -> pd.Series:
        hist = self._history(symbol, "5d", "1d")
        last = hist.iloc[-1]
        prev_close = float(hist["close"].iloc[-2]) if len(hist) >= 2 else float(last["close"])
        price = float(last["close"])
        change = price - prev_close
        change_pct = (change / prev_close * 100.0) if prev_close else float("nan")
        return pd.Series(
            {
                "open": float(last["open"]),
                "high": float(last["high"]),
                "low": float(last["low"]),
                "price": price,
                "volume": int(last["volume"]),
                "previous close": prev_close,
                "change": change,
                "change percent": change_pct,
                "latest trading day": hist.index[-1].date().isoformat(),
            },
            name=symbol,
        )

    def search_symbols(self, keywords: str) -> pd.DataFrame:
        try:
            quotes = getattr(self._yf.Search(keywords), "quotes", []) or []
        except Exception:  # pragma: no cover - network/version dependent
            quotes = []
        rows = [
            {
                "symbol": q.get("symbol"),
                "name": q.get("shortname") or q.get("longname"),
                "type": q.get("quoteType"),
                "region": q.get("exchange"),
                "currency": q.get("currency", ""),
                "matchScore": q.get("score", ""),
            }
            for q in quotes
        ]
        return pd.DataFrame(rows)

    def get_fx_rate(self, from_currency: str, to_currency: str) -> float:
        pair = f"{from_currency}{to_currency}=X"
        return float(self._history(pair, "5d", "1d")["close"].iloc[-1])

    def get_fx_daily(self, from_symbol: str, to_symbol: str, outputsize: str = "compact") -> pd.DataFrame:
        pair = f"{from_symbol}{to_symbol}=X"
        period = "1y" if outputsize == "compact" else "max"
        return self._history(pair, period, "1d")

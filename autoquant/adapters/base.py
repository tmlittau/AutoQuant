"""Adapter interface shared by all market-data providers.

A :class:`MarketDataAdapter` exposes a small set of *primitive* methods that each
concrete provider must implement, plus *composite* methods with default
implementations built on the primitives. Calling code (portfolio, signals, the
notebooks) depends only on this interface, so swapping Alpha Vantage for yfinance
is a one-line change.

All adapters return the same shapes:
  * OHLCV history -> DataFrame[open, high, low, close, volume], sorted DatetimeIndex
    named ``date``.
  * quote -> Series(price, change, change percent, volume, latest trading day, ...).
  * close prices -> DataFrame (one column per symbol), DatetimeIndex named ``date``.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Iterable

import pandas as pd


class DataUnavailableError(RuntimeError):
    """Raised when an adapter cannot return data for a symbol (unknown/empty)."""


class MarketDataAdapter(ABC):
    """Provider-agnostic market-data interface."""

    name: str = "base"

    # ------------------------------------------------------------------ #
    # Primitives -- each provider implements these.
    # ------------------------------------------------------------------ #
    @abstractmethod
    def get_daily(self, symbol: str, outputsize: str = "compact") -> pd.DataFrame:
        ...

    @abstractmethod
    def get_weekly(self, symbol: str) -> pd.DataFrame:
        ...

    @abstractmethod
    def get_monthly(self, symbol: str) -> pd.DataFrame:
        ...

    @abstractmethod
    def get_quote(self, symbol: str) -> pd.Series:
        ...

    @abstractmethod
    def search_symbols(self, keywords: str) -> pd.DataFrame:
        ...

    @abstractmethod
    def get_fx_rate(self, from_currency: str, to_currency: str) -> float:
        ...

    @abstractmethod
    def get_fx_daily(self, from_symbol: str, to_symbol: str, outputsize: str = "compact") -> pd.DataFrame:
        ...

    def resolve(self, symbol: str) -> str:
        """Map a canonical symbol to this provider's symbol (default: identity)."""
        return symbol

    # ------------------------------------------------------------------ #
    # Composites -- shared default implementations.
    # ------------------------------------------------------------------ #
    def get_history(self, symbol: str, interval: str = "daily") -> pd.DataFrame:
        if interval == "daily":
            return self.get_daily(symbol)
        if interval == "weekly":
            return self.get_weekly(symbol)
        if interval == "monthly":
            return self.get_monthly(symbol)
        raise ValueError(f"interval must be daily/weekly/monthly, got {interval!r}")

    def get_quotes(self, symbols: Iterable[str]) -> pd.DataFrame:
        frame = pd.DataFrame([self.get_quote(s) for s in symbols])
        frame.index.name = "symbol"
        return frame

    def get_close_prices(self, symbols: Iterable[str], interval: str = "daily") -> pd.DataFrame:
        closes = {s: self.get_history(s, interval=interval)["close"] for s in symbols}
        frame = pd.DataFrame(closes).sort_index()
        frame.index.name = "date"
        return frame

    def eur_per_usd_series(self) -> pd.Series:
        """Historical daily USD->EUR close (EUR per 1 USD)."""
        return self.get_fx_daily("USD", "EUR")["close"].rename("eur_per_usd")

    def latest_eur_per_usd(self) -> float:
        """Latest USD->EUR rate (EUR per 1 USD)."""
        return self.get_fx_rate("USD", "EUR")

    @staticmethod
    def to_eur(prices_usd: pd.Series, eur_per_usd: pd.Series) -> pd.Series:
        """Convert a USD price series to EUR using a daily FX series (date-aligned)."""
        fx_aligned = eur_per_usd.reindex(prices_usd.index).ffill().bfill()
        return prices_usd * fx_aligned

    def __repr__(self) -> str:  # pragma: no cover - cosmetic
        return f"<{type(self).__name__} name={self.name!r}>"

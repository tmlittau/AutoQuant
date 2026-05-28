"""Alpha Vantage adapter -- a thin OO wrapper over the existing data/fx modules."""

from __future__ import annotations

from typing import Optional

import pandas as pd

from ..client import AlphaVantageClient
from ..data import (
    get_daily,
    get_monthly,
    get_quote,
    get_weekly,
    search_symbols,
)
from ..fx import get_fx_daily, get_fx_rate
from .base import MarketDataAdapter


class AlphaVantageAdapter(MarketDataAdapter):
    """Market data via Alpha Vantage (cached, rate-limit-aware client)."""

    name = "alphavantage"

    def __init__(self, client: Optional[AlphaVantageClient] = None, **client_kwargs) -> None:
        self.client = client or AlphaVantageClient(**client_kwargs)

    def get_daily(self, symbol: str, outputsize: str = "compact") -> pd.DataFrame:
        return get_daily(self.client, symbol, outputsize=outputsize)

    def get_weekly(self, symbol: str) -> pd.DataFrame:
        return get_weekly(self.client, symbol)

    def get_monthly(self, symbol: str) -> pd.DataFrame:
        return get_monthly(self.client, symbol)

    def get_quote(self, symbol: str) -> pd.Series:
        return get_quote(self.client, symbol)

    def search_symbols(self, keywords: str) -> pd.DataFrame:
        return search_symbols(self.client, keywords)

    def get_fx_rate(self, from_currency: str, to_currency: str) -> float:
        return get_fx_rate(self.client, from_currency, to_currency)

    def get_fx_daily(self, from_symbol: str, to_symbol: str, outputsize: str = "compact") -> pd.DataFrame:
        return get_fx_daily(self.client, from_symbol, to_symbol, outputsize=outputsize)

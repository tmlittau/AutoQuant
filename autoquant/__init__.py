"""AutoQuant: pull market data (Alpha Vantage or yfinance) and compute metrics."""

from __future__ import annotations

from . import adapters, fx, metrics, portfolio, signals
from .adapters import (
    AlphaVantageAdapter,
    DataUnavailableError,
    MarketDataAdapter,
    get_adapter,
)
from .client import (
    AlphaVantageClient,
    AlphaVantageError,
    RateLimitError,
    load_api_key,
)
from .data import (
    default_client,
    get_close_prices,
    get_daily,
    get_history,
    get_monthly,
    get_quote,
    get_quotes,
    get_weekly,
    search_symbols,
)
from .portfolio import load_portfolio

__all__ = [
    # adapters (preferred interface)
    "get_adapter",
    "MarketDataAdapter",
    "AlphaVantageAdapter",
    "YFinanceAdapter",
    "DataUnavailableError",
    "adapters",
    # Alpha Vantage client (used by the AV adapter)
    "AlphaVantageClient",
    "AlphaVantageError",
    "RateLimitError",
    "load_api_key",
    "default_client",
    # low-level AV functions (back-compat; prefer an adapter)
    "get_daily",
    "get_weekly",
    "get_monthly",
    "get_history",
    "get_quote",
    "get_quotes",
    "get_close_prices",
    "search_symbols",
    # analytics
    "metrics",
    "fx",
    "portfolio",
    "signals",
    "load_portfolio",
]


def __getattr__(name: str):
    # Lazily expose YFinanceAdapter so importing autoquant never requires yfinance.
    if name == "YFinanceAdapter":
        from .adapters.yfinance_adapter import YFinanceAdapter

        return YFinanceAdapter
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__version__ = "0.2.0"

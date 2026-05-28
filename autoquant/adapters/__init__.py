"""Pluggable market-data adapters (Alpha Vantage, yfinance)."""

from __future__ import annotations

from .alphavantage import AlphaVantageAdapter
from .base import DataUnavailableError, MarketDataAdapter

_ALIASES = {
    "alphavantage": "alphavantage",
    "alpha_vantage": "alphavantage",
    "alpha": "alphavantage",
    "av": "alphavantage",
    "yfinance": "yfinance",
    "yahoo": "yfinance",
    "yf": "yfinance",
}


def get_adapter(name: str = "alphavantage", **kwargs) -> MarketDataAdapter:
    """Build a market-data adapter by name.

    ``name`` accepts ``"alphavantage"`` (aliases: av, alpha) or ``"yfinance"``
    (aliases: yf, yahoo). Extra kwargs are forwarded to the adapter constructor.
    """
    key = _ALIASES.get(name.lower().strip())
    if key == "alphavantage":
        return AlphaVantageAdapter(**kwargs)
    if key == "yfinance":
        from .yfinance_adapter import YFinanceAdapter  # lazy: yfinance optional

        return YFinanceAdapter(**kwargs)
    raise ValueError(
        f"Unknown adapter {name!r}. Use 'alphavantage' (av) or 'yfinance' (yf)."
    )


def __getattr__(name: str):
    # Lazily expose YFinanceAdapter so importing this package never requires yfinance.
    if name == "YFinanceAdapter":
        from .yfinance_adapter import YFinanceAdapter

        return YFinanceAdapter
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "MarketDataAdapter",
    "AlphaVantageAdapter",
    "YFinanceAdapter",
    "DataUnavailableError",
    "get_adapter",
]

"""Process-wide market-data adapter, with Alpha Vantage quota tracking.

One instance lives on ``BackendConfig.adapter_registry`` (set up in
:meth:`portfolio_app.apps.PortfolioAppConfig.ready`). API views call
:func:`get_registry` to reach it. Sharing one adapter means the yfinance
in-memory cache (``YFinanceAdapter._cache``) is actually reused across requests.
"""

from __future__ import annotations

from typing import Any

import autoquant as aq
from autoquant.adapters.base import MarketDataAdapter


class AdapterRegistry:
    """Holds the active adapter; supports hot-swap and quota introspection."""

    AV_LIMIT = 25  # free-tier daily cap

    def __init__(self, initial: str = "yfinance", **kwargs: Any) -> None:
        self._name = initial
        self._adapter: MarketDataAdapter = aq.get_adapter(initial, **kwargs)

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #
    @property
    def name(self) -> str:
        return self._name

    @property
    def adapter(self) -> MarketDataAdapter:
        return self._adapter

    def swap(self, name: str, **kwargs: Any) -> None:
        """Replace the active adapter (e.g. yfinance <-> alphavantage)."""
        self._adapter = aq.get_adapter(name, **kwargs)
        self._name = name

    def clear_cache(self) -> None:
        """Drop the yfinance in-memory cache; AV uses its own on-disk cache."""
        cache = getattr(self._adapter, "_cache", None)
        if isinstance(cache, dict):
            cache.clear()

    # ------------------------------------------------------------------ #
    # Alpha Vantage quota (persisted to a Setting row so a restart keeps state)
    # ------------------------------------------------------------------ #
    def av_quota_status(self) -> dict:
        from django.utils import timezone

        from .models import Setting

        row = Setting.objects.filter(key="av_quota").first()
        today = timezone.now().date().isoformat()
        if row is None or (row.value or {}).get("date") != today:
            return {
                "used": 0,
                "limit": self.AV_LIMIT,
                "last_call_at": None,
                "resets_at": None,
            }
        val = row.value
        return {
            "used": int(val.get("used", 0)),
            "limit": self.AV_LIMIT,
            "last_call_at": val.get("last_call_at"),
            "resets_at": val.get("resets_at"),
        }

    def record_av_call(self) -> None:
        """Bump the AV daily counter; resets at calendar-day boundary."""
        from django.utils import timezone

        from .models import Setting

        now = timezone.now()
        today = now.date().isoformat()
        row, _ = Setting.objects.get_or_create(key="av_quota", defaults={"value": {}})
        val = dict(row.value or {})
        if val.get("date") != today:
            val = {"date": today, "used": 0}
        val["used"] = int(val.get("used", 0)) + 1
        val["last_call_at"] = now.isoformat()
        row.value = val
        row.save(update_fields=["value", "updated_at"])


def get_registry() -> AdapterRegistry:
    """Return the singleton registry built in apps.ready()."""
    from django.apps import apps

    cfg = apps.get_app_config("portfolio_app")
    return cfg.adapter_registry  # type: ignore[attr-defined]

"""App config: builds the process-wide adapter singleton at startup."""

from __future__ import annotations

import os

from django.apps import AppConfig


class PortfolioAppConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "portfolio_app"

    adapter_registry = None  # populated in ready()

    def ready(self) -> None:
        # Management commands like `makemigrations` import models before any DB
        # exists; skip building the adapter (which loads autoquant + yfinance) in
        # those cases. Set AUTOQUANT_NO_ADAPTER=1 to force-skip too.
        if os.environ.get("AUTOQUANT_NO_ADAPTER"):
            return

        from .registry import AdapterRegistry

        try:
            self.adapter_registry = AdapterRegistry(initial="yfinance")
        except Exception as exc:  # pragma: no cover - defensive
            # Don't block migrate/collectstatic on a transient adapter failure.
            import logging

            logging.getLogger(__name__).warning("AdapterRegistry init failed: %s", exc)
            self.adapter_registry = None

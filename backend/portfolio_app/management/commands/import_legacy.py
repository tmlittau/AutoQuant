"""One-shot migration: read existing portfolio.yaml + watchlist.yaml +
data/transactions.csv (from the autoquant project root) into the database.

Idempotent in the sense that it refuses to run against a non-empty DB unless
``--force`` is passed (which wipes first). Designed to be re-runnable while we
still have the legacy files; after the webapp goes live the DB becomes the
source of truth.

Usage:
    python manage.py import_legacy            # safe; refuses if rows exist
    python manage.py import_legacy --force    # wipes + reimports
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pandas as pd
from django.core.management.base import BaseCommand
from django.db import transaction as db_transaction

import autoquant as aq
from autoquant import portfolio as pf
from autoquant.client import PROJECT_ROOT

from portfolio_app.models import (
    AssetClass,
    GroupConfig,
    Holding,
    HoldingKind,
    Transaction,
)
from portfolio_app.repository import _to_decimal


class Command(BaseCommand):
    help = "Import the legacy YAML + CSV state into the database."

    def add_arguments(self, parser):
        parser.add_argument(
            "--force",
            action="store_true",
            help="Wipe existing Holding / GroupConfig / Transaction rows first.",
        )

    def handle(self, *args, force: bool = False, **opts):
        existing = (
            Holding.objects.exists()
            or Transaction.objects.exists()
            or GroupConfig.objects.exists()
        )
        if existing and not force:
            self.stdout.write(
                self.style.WARNING(
                    "DB already populated. Pass --force to wipe and reimport."
                )
            )
            return

        with db_transaction.atomic():
            if existing:
                self.stdout.write("Wiping existing rows...")
                Transaction.objects.all().delete()
                Holding.objects.all().delete()
                GroupConfig.objects.all().delete()

            self._import_yaml("portfolio.yaml", HoldingKind.PORTFOLIO)
            self._import_yaml("watchlist.yaml", HoldingKind.WATCHLIST)
            self._import_transactions()

        self.stdout.write(
            self.style.SUCCESS(
                f"Imported {Holding.objects.count()} holdings, "
                f"{GroupConfig.objects.count()} groups, "
                f"{Transaction.objects.count()} transactions."
            )
        )

    # ------------------------------------------------------------------ #
    # Stocks + ETF holdings (YAML)
    # ------------------------------------------------------------------ #
    def _import_yaml(self, filename: str, kind: str) -> None:
        path = PROJECT_ROOT / filename
        if not path.is_file():
            self.stdout.write(self.style.WARNING(f"  {path} not found, skipping."))
            return

        cfg = aq.load_portfolio(path)

        # --- Stock groups (with target weights / description) ---
        stocks = (cfg.get("stocks") or {}).get("groups") or {}
        for group_name, gdef in stocks.items():
            gdef = gdef or {}
            gc, created = GroupConfig.objects.get_or_create(
                asset_class=AssetClass.STOCKS,
                name=group_name,
                defaults={
                    "description": gdef.get("description", "") or "",
                    "target_weight": _to_decimal(gdef.get("target_weight")),
                },
            )
            # If the row already existed (e.g. portfolio + watchlist share group
            # names), keep whichever value is present.
            if not created:
                if gdef.get("description") and not gc.description:
                    gc.description = gdef["description"]
                if gdef.get("target_weight") is not None and gc.target_weight is None:
                    gc.target_weight = _to_decimal(gdef["target_weight"])
                gc.save()

            for h in gdef.get("holdings", []) or []:
                Holding.objects.update_or_create(
                    kind=kind,
                    ticker=h["ticker"],
                    defaults={
                        "asset_class": AssetClass.STOCKS,
                        "group": group_name,
                        "name": h.get("name", h["ticker"]),
                        "currency": h.get("currency", "USD"),
                    },
                )

        # --- ETF holdings (flat list) ---
        etfs = cfg.get("etfs") or {}
        if etfs.get("holdings"):
            gc, _ = GroupConfig.objects.get_or_create(
                asset_class=AssetClass.ETFS,
                name="ETFs",
                defaults={"description": etfs.get("description", "") or ""},
            )
            if etfs.get("description") and not gc.description:
                gc.description = etfs["description"]
                gc.save()

            for h in etfs["holdings"]:
                Holding.objects.update_or_create(
                    kind=kind,
                    ticker=h["ticker"],
                    defaults={
                        "asset_class": AssetClass.ETFS,
                        "group": "ETFs",
                        "name": h.get("name", h["ticker"]),
                        "currency": h.get("currency", "EUR"),
                    },
                )

        n_h = Holding.objects.filter(kind=kind).count()
        self.stdout.write(f"  {filename}: imported holdings -> {n_h} rows now ({kind})")

    # ------------------------------------------------------------------ #
    # Transactions (CSV)
    # ------------------------------------------------------------------ #
    def _import_transactions(self) -> None:
        path = pf.DEFAULT_LEDGER_PATH
        if not path.is_file():
            self.stdout.write(self.style.WARNING(f"  {path} not found, skipping."))
            return

        df = pf.load_transactions(path)
        if df.empty:
            self.stdout.write(f"  {path.name}: 0 rows.")
            return

        for _, row in df.iterrows():
            date_val = row["date"]
            if hasattr(date_val, "date"):
                date_val = date_val.date()
            Transaction.objects.create(
                date=date_val,
                ticker=row["ticker"],
                group=row["group"],
                action=str(row["action"]).lower(),
                amount_eur=_to_decimal(row["amount_eur"]),
                price_local=_to_decimal(row["price_local"]),
                listing_currency=str(row["listing_currency"]),
                eur_per_local=_to_decimal(row["eur_per_local"]),
                shares=_to_decimal(row["shares"]),
                fee_eur=_to_decimal(row.get("fee_eur", 0)) or Decimal("0"),
                note=str(row.get("note", "") or ""),
            )
        self.stdout.write(f"  transactions.csv: imported {len(df)} rows.")

"""Database schema for the AutoQuant webapp (DB is source of truth).

Mirrors the shape autoquant.portfolio expects after the repository layer marshals
it back into the dict/DataFrame form, so the existing analytics code is reused
unchanged. Money/shares are stored as Decimal to avoid float drift; pandas-bound
code converts to float at the boundary.
"""

from __future__ import annotations

from decimal import Decimal

from django.db import models


class AssetClass(models.TextChoices):
    STOCKS = "stocks", "Stocks"
    ETFS = "etfs", "ETFs"
    CRYPTO = "crypto", "Crypto"


class HoldingKind(models.TextChoices):
    PORTFOLIO = "portfolio", "Portfolio"
    WATCHLIST = "watchlist", "Watchlist"


class TransactionAction(models.TextChoices):
    BUY = "buy", "Buy"
    SELL = "sell", "Sell"


class GroupConfig(models.Model):
    """Per-(asset_class, group_name) metadata: description + optional target weight.

    Target weight only meaningful for the stock portfolio (ETF group is flat).
    """

    asset_class = models.CharField(max_length=10, choices=AssetClass.choices)
    name = models.CharField(max_length=64)
    description = models.TextField(blank=True, default="")
    target_weight = models.DecimalField(
        max_digits=5, decimal_places=4, null=True, blank=True
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["asset_class", "name"], name="unique_group_per_asset_class"
            ),
        ]
        ordering = ["asset_class", "name"]

    def __str__(self) -> str:  # pragma: no cover
        return f"{self.asset_class}:{self.name}"


class Holding(models.Model):
    """A ticker that's part of the portfolio OR the watchlist.

    The same ticker can be in both kinds (own AAPL AND watch it for re-entry),
    so uniqueness is on (kind, ticker).
    """

    kind = models.CharField(max_length=10, choices=HoldingKind.choices)
    asset_class = models.CharField(max_length=10, choices=AssetClass.choices)
    group = models.CharField(max_length=64)       # sector name; "ETFs" for ETF holdings
    ticker = models.CharField(max_length=32)
    name = models.CharField(max_length=128)
    currency = models.CharField(max_length=8, default="USD")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["kind", "ticker"], name="unique_ticker_per_kind"),
        ]
        ordering = ["kind", "group", "ticker"]

    def __str__(self) -> str:  # pragma: no cover
        return f"{self.kind}:{self.ticker}"


class Transaction(models.Model):
    """A single buy or sell on a portfolio holding.

    ``amount_eur`` and ``shares`` are signed: positive = buy (cash in / shares in),
    negative = sell. Group/ticker are denormalised from Holding for cheap
    aggregation (and so deleting a Holding doesn't cascade away history).
    """

    date = models.DateField()
    ticker = models.CharField(max_length=32, db_index=True)
    group = models.CharField(max_length=64)
    action = models.CharField(max_length=4, choices=TransactionAction.choices)
    amount_eur = models.DecimalField(max_digits=14, decimal_places=4)        # signed
    price_local = models.DecimalField(max_digits=14, decimal_places=6)
    listing_currency = models.CharField(max_length=8)
    eur_per_local = models.DecimalField(max_digits=12, decimal_places=8)
    shares = models.DecimalField(max_digits=18, decimal_places=8)            # signed
    fee_eur = models.DecimalField(
        max_digits=10, decimal_places=4, default=Decimal("0")
    )
    note = models.CharField(max_length=255, blank=True, default="")
    # Links the two halves of a swap (a sell of one coin + a buy of another in
    # a single user action, e.g. 1000 USDC -> 0.025 BTC). NULL for ordinary
    # buys/sells. Both rows of a swap share the same UUID so the ledger can
    # collapse them into one displayed event.
    swap_group_id = models.UUIDField(null=True, blank=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["date", "created_at"]

    def __str__(self) -> str:  # pragma: no cover
        return f"{self.date} {self.action} {self.ticker} {self.amount_eur}EUR"


class AuditEntry(models.Model):
    """Append-only log of every mutation, for debuggability / undo (Phase 8)."""

    timestamp = models.DateTimeField(auto_now_add=True)
    user = models.CharField(max_length=150, blank=True, default="")
    endpoint = models.CharField(max_length=128)
    method = models.CharField(max_length=8)
    payload_diff = models.JSONField(default=dict)

    class Meta:
        ordering = ["-timestamp"]
        verbose_name_plural = "Audit entries"


class Setting(models.Model):
    """Key/value JSON settings (active adapter, AV API key, AV quota state, ...)."""

    key = models.CharField(max_length=64, unique=True)
    value = models.JSONField()
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["key"]

    def __str__(self) -> str:  # pragma: no cover
        return f"Setting({self.key})"

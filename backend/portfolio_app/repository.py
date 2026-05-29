"""Repository layer: marshals DB rows into the dict/DataFrame shapes that
``autoquant.portfolio`` already understands, and writes mutations back.

API views call these functions exclusively -- they never touch the ORM. That
way all the existing analytics (`current_positions`, `value_history`,
`correlation_matrix`, `signals.portfolio_signals`, ...) keep working unchanged.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Optional

import pandas as pd

from autoquant import portfolio as pf

from .models import (
    AssetClass,
    GroupConfig,
    Holding,
    HoldingKind,
    Transaction,
    TransactionAction,
)

# Columns that come out of Transaction.objects.values() in repo order.
_TX_DB_COLUMNS = [
    "date",
    "ticker",
    "group",
    "action",
    "amount_eur",
    "price_local",
    "listing_currency",
    "eur_per_local",
    "shares",
    "fee_eur",
    "note",
]


# --------------------------------------------------------------------------- #
# Read: DB -> dict / DataFrame (the shapes autoquant.portfolio expects)
# --------------------------------------------------------------------------- #
def build_portfolio_dict(kind: str = HoldingKind.PORTFOLIO) -> dict:
    """Build a YAML-shaped portfolio/watchlist dict from the database.

    Output mirrors ``aq.load_portfolio()``: top-level ``base_currency``,
    ``quote_currency``, plus one section per asset class:

    - ``stocks`` keeps the legacy ``{groups: {...}}`` structure (target
      weights, descriptions per sector).
    - ``etfs`` and ``crypto`` use the flat ``{holdings: [...]}`` shape -- no
      sub-categories, no target weights. Both are pinned to a single
      canonical group name (``ETFs`` / ``Crypto``) on the Holding row.

    Empty sections are included so downstream callers can safely index.
    """
    out: dict[str, Any] = {
        "base_currency": "EUR",
        "quote_currency": "USD",
        "stocks": {"groups": {}},
        "etfs": {"holdings": []},
        "crypto": {"holdings": []},
    }

    # Per-group metadata (description, target_weight) keyed by (asset_class, name).
    group_meta = {(gc.asset_class, gc.name): gc for gc in GroupConfig.objects.all()}

    for h in Holding.objects.filter(kind=kind).order_by("group", "ticker"):
        holding_dict = {"ticker": h.ticker, "name": h.name, "currency": h.currency}
        if h.asset_class == AssetClass.STOCKS:
            groups = out["stocks"]["groups"]
            if h.group not in groups:
                gc = group_meta.get((AssetClass.STOCKS, h.group))
                groups[h.group] = {"holdings": []}
                if gc is not None:
                    if gc.description:
                        groups[h.group]["description"] = gc.description
                    if gc.target_weight is not None:
                        groups[h.group]["target_weight"] = float(gc.target_weight)
            groups[h.group]["holdings"].append(holding_dict)
        elif h.asset_class == AssetClass.ETFS:
            out["etfs"]["holdings"].append(holding_dict)
        elif h.asset_class == AssetClass.CRYPTO:
            out["crypto"]["holdings"].append(holding_dict)

    etf_meta = group_meta.get((AssetClass.ETFS, "ETFs"))
    if etf_meta and etf_meta.description:
        out["etfs"]["description"] = etf_meta.description
    crypto_meta = group_meta.get((AssetClass.CRYPTO, "Crypto"))
    if crypto_meta and crypto_meta.description:
        out["crypto"]["description"] = crypto_meta.description

    return out


def build_transactions_df() -> pd.DataFrame:
    """Build a transactions DataFrame matching ``pf.TRANSACTION_COLUMNS`` order."""
    rows = list(Transaction.objects.all().values(*_TX_DB_COLUMNS))
    if not rows:
        return pf.empty_transactions()
    df = pd.DataFrame(rows)
    # Decimal columns -> float so downstream pandas math doesn't choke.
    for col in ("amount_eur", "price_local", "eur_per_local", "shares", "fee_eur"):
        df[col] = df[col].astype(float)
    df["date"] = pd.to_datetime(df["date"])
    return (
        df.reindex(columns=pf.TRANSACTION_COLUMNS)
        .sort_values("date")
        .reset_index(drop=True)
    )


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _to_decimal(value: Any) -> Optional[Decimal]:
    """Coerce floats / strings / NaN-ish to ``Decimal`` (or ``None``)."""
    if value is None:
        return None
    if isinstance(value, float) and pd.isna(value):
        return None
    return Decimal(str(value))


def get_current_position(ticker: str) -> tuple[Decimal, Decimal]:
    """Return ``(net_shares, net_cost_eur)`` for ``ticker`` summing every
    Transaction row. Signs are preserved (buys positive, sells negative)
    so a long position has positive shares, a sold-out one has 0, and
    something that's been over-sold has negative (an error in the user's
    ledger but the math is honest).

    Used by:
      * the "Sell all" shortcut in the Add-Investment modal,
      * the "+ Log sell" affordance on the Stock single-page view (only
        shown when net_shares > 0),
      * the future sell endpoint when shares are submitted instead of EUR.
    """
    from django.db.models import Sum

    agg = Transaction.objects.filter(ticker=ticker).aggregate(
        net_shares=Sum("shares"),
        net_cost=Sum("amount_eur"),
    )
    return (
        agg["net_shares"] or Decimal("0"),
        agg["net_cost"] or Decimal("0"),
    )


def infer_asset_class(group_name: str) -> str:
    """Heuristic used by the CSV import endpoint to guess the asset_class for
    a Holding it has to auto-create. Matches the convention already encoded in
    the import_legacy command and the YAML schema:

    - ``"ETFs"`` (case-insensitive) -> etfs
    - ``"Crypto"`` (or any group ending in "/Crypto") -> crypto
    - everything else -> stocks (sector-style group names)
    """
    g = (group_name or "").strip().lower()
    if g == "etfs":
        return "etfs"
    if g == "crypto" or g.endswith("/crypto"):
        return "crypto"
    return "stocks"

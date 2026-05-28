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
    ``quote_currency``, ``stocks: {groups: {...}}``, ``etfs: {holdings: [...]}``.
    Empty sections are included so downstream callers can safely index.
    """
    out: dict[str, Any] = {
        "base_currency": "EUR",
        "quote_currency": "USD",
        "stocks": {"groups": {}},
        "etfs": {"holdings": []},
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
        else:  # ETF
            out["etfs"]["holdings"].append(holding_dict)

    etf_meta = group_meta.get((AssetClass.ETFS, "ETFs"))
    if etf_meta and etf_meta.description:
        out["etfs"]["description"] = etf_meta.description

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

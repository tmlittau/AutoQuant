"""JSON-safe conversions for pandas / numpy / Decimal objects.

The autoquant analytics functions return ``pd.DataFrame`` / ``pd.Series`` /
plain dicts. django-ninja serialises via Pydantic v2, which rejects ``NaN``,
``inf``, ``np.float64`` and ``pd.Timestamp`` by default. These helpers do the
boundary conversion once -- everything else in api.py is then plain Python.
"""

from __future__ import annotations

import math
from datetime import date, datetime
from decimal import Decimal
from typing import Any

import numpy as np
import pandas as pd


def safe(value: Any) -> Any:
    """Make a single scalar JSON-safe (``NaN``/``inf`` -> ``None``, numpy/Decimal/Timestamp -> native)."""
    if value is None:
        return None
    if isinstance(value, bool):  # before int check
        return value
    if isinstance(value, (np.bool_,)):
        return bool(value)
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, Decimal):
        try:
            value = float(value)
        except (TypeError, ValueError):
            return None
    if isinstance(value, (np.floating, float)):
        f = float(value)
        if math.isnan(f) or math.isinf(f):
            return None
        return f
    if isinstance(value, (pd.Timestamp, datetime)):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, np.ndarray):
        return [safe(v) for v in value.tolist()]
    return value


def safe_float(value: Any) -> float | None:
    """Coerce to ``float`` or ``None`` (used for typed schemas that allow Optional[float])."""
    s = safe(value)
    return s if isinstance(s, (int, float)) and not isinstance(s, bool) else None


def frame_to_split(df: pd.DataFrame) -> dict:
    """``DataFrame`` -> ``{index, columns, data}`` split-orient, JSON-safe."""
    return {
        "index": [safe(i) for i in df.index],
        "columns": [str(c) for c in df.columns],
        "data": [[safe(v) for v in row] for row in df.itertuples(index=False, name=None)],
    }


def frame_to_records(df: pd.DataFrame, *, index_key: str | None = None) -> list[dict]:
    """``DataFrame`` -> list of row dicts (one per row)."""
    out: list[dict] = []
    for idx, row in df.iterrows():
        rec: dict[str, Any] = {}
        if index_key:
            rec[index_key] = safe(idx)
        for col, val in row.items():
            rec[str(col)] = safe(val)
        out.append(rec)
    return out


def series_to_list(s: pd.Series) -> list:
    """``Series`` -> list of JSON-safe values (in index order)."""
    return [safe(v) for v in s.values]


def series_to_dict(s: pd.Series) -> dict[str, Any]:
    """``Series`` -> ``{name, index, values}`` (index order)."""
    return {
        "name": str(s.name) if s.name is not None else None,
        "index": [safe(i) for i in s.index],
        "values": series_to_list(s),
    }


def clean(value: Any) -> Any:
    """Recursively make any pandas/numpy/Decimal object tree JSON-safe."""
    if isinstance(value, dict):
        return {str(k): clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [clean(v) for v in value]
    if isinstance(value, pd.DataFrame):
        return frame_to_split(value)
    if isinstance(value, pd.Series):
        return series_to_dict(value)
    return safe(value)

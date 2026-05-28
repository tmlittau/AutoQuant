"""Tiny audit helper used by mutating API endpoints.

Each ``POST/PATCH/DELETE`` route calls :func:`audit` with the request, endpoint
path, method, and a (sanitised) payload diff. Rows land in ``AuditEntry`` and
are visible in the Django admin.
"""

from __future__ import annotations

from typing import Any

_REDACT_KEYS = {"av_api_key", "password"}


def _sanitize(value: Any) -> Any:
    """Mask secrets in a payload before storing it in the audit log."""
    if isinstance(value, dict):
        return {
            k: ("***" if k in _REDACT_KEYS and v else _sanitize(v))
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [_sanitize(v) for v in value]
    return value


def audit(request, endpoint: str, method: str, payload_diff: Any = None) -> None:
    """Append an AuditEntry row for a mutation."""
    from .models import AuditEntry

    user_name = ""
    if hasattr(request, "user") and getattr(request.user, "is_authenticated", False):
        user_name = request.user.username

    AuditEntry.objects.create(
        user=user_name,
        endpoint=endpoint,
        method=method,
        payload_diff=_sanitize(payload_diff) if payload_diff is not None else {},
    )

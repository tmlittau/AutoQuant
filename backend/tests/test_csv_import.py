"""Tests for ``POST /api/transactions/import``.

Covers header validation, row validation in strict + non-strict modes,
append vs replace, dedup on append, holdings auto-create, and atomic
rollback on a bad row.
"""

from __future__ import annotations

import io
from decimal import Decimal

import pytest

from portfolio_app.models import (
    AssetClass,
    Holding,
    HoldingKind,
    Transaction,
)


CANONICAL_HEADER = (
    "date,group,ticker,action,amount_eur,price_local,"
    "listing_currency,eur_per_local,shares,fee_eur,note"
)


def _csv(rows: list[str], header: str = CANONICAL_HEADER) -> io.BytesIO:
    body = header + "\n" + "\n".join(rows) + ("\n" if rows else "")
    return io.BytesIO(body.encode())


def _post(client, csrftoken, file: io.BytesIO, mode: str = "append", strict: bool = True):
    file.name = "transactions.csv"
    return client.post(
        f"/api/transactions/import?mode={mode}&strict={'true' if strict else 'false'}",
        data={"file": file},
        HTTP_X_CSRFTOKEN=csrftoken,
    )


@pytest.mark.django_db
class TestCsvImport:
    def test_rejects_missing_columns(self, authed_client, csrftoken):
        bad = "date,ticker,action\n2026-05-29,AAPL,buy"
        file = io.BytesIO(bad.encode())
        file.name = "transactions.csv"
        r = authed_client.post(
            "/api/transactions/import?mode=append",
            data={"file": file},
            HTTP_X_CSRFTOKEN=csrftoken,
        )
        assert r.status_code == 400
        assert "header" in r.json()["detail"].lower()

    def test_rejects_unknown_mode(self, authed_client, csrftoken):
        file = _csv([])
        file.name = "transactions.csv"
        r = authed_client.post(
            "/api/transactions/import?mode=teleport",
            data={"file": file},
            HTTP_X_CSRFTOKEN=csrftoken,
        )
        assert r.status_code == 400

    def test_append_creates_rows_and_auto_creates_holding(
        self, authed_client, csrftoken
    ):
        assert Transaction.objects.count() == 0
        assert Holding.objects.count() == 0

        row = (
            "2026-05-29,Tech,AAPL,buy,100.00,180.50,USD,0.92,0.4978,0.00,initial"
        )
        r = _post(authed_client, csrftoken, _csv([row]))
        assert r.status_code == 200, r.content
        body = r.json()
        assert body["imported"] == 1
        assert body["skipped"] == 0
        assert body["errors"] == []
        assert body["holdings_created"] == ["AAPL"]

        # Holding was auto-created with the inferred stocks asset class.
        h = Holding.objects.get(ticker="AAPL")
        assert h.asset_class == AssetClass.STOCKS
        assert h.kind == HoldingKind.PORTFOLIO
        assert h.group == "Tech"
        assert h.currency == "USD"

        # Transaction landed.
        tx = Transaction.objects.get(ticker="AAPL")
        assert tx.action == "buy"
        assert tx.amount_eur == Decimal("100.0000")

    def test_append_skips_duplicates(self, authed_client, csrftoken):
        row = (
            "2026-05-29,Tech,AAPL,buy,100.00,180.50,USD,0.92,0.4978,0.00,initial"
        )
        r1 = _post(authed_client, csrftoken, _csv([row]))
        assert r1.json()["imported"] == 1

        # Re-importing the same row in append mode should be a no-op (dedup).
        r2 = _post(authed_client, csrftoken, _csv([row]))
        body2 = r2.json()
        assert body2["imported"] == 0
        assert body2["skipped"] == 1
        assert Transaction.objects.filter(ticker="AAPL").count() == 1

    def test_replace_wipes_existing(self, authed_client, csrftoken):
        row1 = (
            "2026-04-01,Tech,AAPL,buy,50.00,170.00,USD,0.92,0.3199,0.00,early"
        )
        _post(authed_client, csrftoken, _csv([row1]))
        assert Transaction.objects.count() == 1

        row2 = (
            "2026-05-29,Tech,AAPL,buy,100.00,180.50,USD,0.92,0.4978,0.00,later"
        )
        r = _post(authed_client, csrftoken, _csv([row2]), mode="replace")
        body = r.json()
        assert body["mode"] == "replace"
        assert body["imported"] == 1
        # Old row gone; only the new one remains.
        assert Transaction.objects.count() == 1
        assert Transaction.objects.first().note == "later"

    def test_infers_etf_asset_class(self, authed_client, csrftoken):
        row = (
            "2026-05-29,ETFs,WEBN.DE,buy,200.00,4.50,EUR,1.0,44.4444,0.00,etf"
        )
        r = _post(authed_client, csrftoken, _csv([row]))
        assert r.status_code == 200
        h = Holding.objects.get(ticker="WEBN.DE")
        assert h.asset_class == AssetClass.ETFS

    def test_strict_mode_aborts_on_bad_row(self, authed_client, csrftoken):
        good = (
            "2026-05-29,Tech,AAPL,buy,100.00,180.50,USD,0.92,0.4978,0.00,ok"
        )
        bad = (
            "2026-05-30,Tech,MSFT,donate,200.00,400.00,USD,0.92,0.4571,0.00,nope"
        )
        r = _post(authed_client, csrftoken, _csv([good, bad]), strict=True)
        # In strict mode a single bad row -> 422, nothing committed.
        assert r.status_code == 422
        body = r.json()
        assert body["imported"] == 0
        assert any("action" in e["message"] for e in body["errors"])
        # Atomic rollback: the good row didn't sneak in.
        assert Transaction.objects.count() == 0
        assert Holding.objects.count() == 0

    def test_non_strict_mode_commits_valid_rows(self, authed_client, csrftoken):
        good = (
            "2026-05-29,Tech,AAPL,buy,100.00,180.50,USD,0.92,0.4978,0.00,ok"
        )
        bad = (
            "2026-05-30,Tech,MSFT,donate,200.00,400.00,USD,0.92,0.4571,0.00,nope"
        )
        r = _post(
            authed_client, csrftoken, _csv([good, bad]), strict=False
        )
        assert r.status_code == 200
        body = r.json()
        assert body["imported"] == 1
        assert len(body["errors"]) == 1
        assert Transaction.objects.count() == 1

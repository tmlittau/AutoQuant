"""Regression tests for the initial-buy / estimate-shares date handling.

Bug: creating a holding (or transaction) with a trade date older than the
adapter's compact (~1y) price window made ``estimate_shares`` produce NaN,
which then crashed the Decimal() conversion with
``"NaN" value must be a decimal number`` -- surfaced to the user as an opaque
``failed to create holding`` 400 (and a 500 on the transaction path).

The fix makes ``estimate_shares`` widen to full history and, failing that,
raise ``DataUnavailableError`` -- which every create path already turns into a
clean, explanatory 400. The stub adapter returns a fixed ~2026-04 window, so a
date before it exercises the "no price, clean 400" branch.
"""

from __future__ import annotations

import json

import pytest

from portfolio_app.models import Holding


def _post(client, path, body, csrftoken):
    return client.post(
        path,
        data=json.dumps(body),
        content_type="application/json",
        HTTP_X_CSRFTOKEN=csrftoken,
    )


@pytest.mark.django_db
class TestEstimateSharesDates:
    def test_holding_with_recent_initial_buy_succeeds(
        self, authed_client, csrftoken, stub_adapter
    ):
        # A date inside the stub's window (2026-04-01 .. ~2026-06) works.
        r = _post(
            authed_client,
            "/api/holdings",
            {
                "kind": "portfolio", "asset_class": "stocks", "group": "Tech",
                "ticker": "MSFT", "name": "Microsoft", "currency": "USD",
                "initial_amount_eur": 300, "initial_date": "2026-04-10",
            },
            csrftoken,
        )
        assert r.status_code == 201, r.content
        assert r.json()["transaction"] is not None

    def test_holding_with_old_initial_buy_returns_clean_400(
        self, authed_client, csrftoken, stub_adapter
    ):
        # A date before the available price history must NOT 500 or leak a
        # "NaN value must be a decimal number" -- it returns a clean 400.
        r = _post(
            authed_client,
            "/api/holdings",
            {
                "kind": "portfolio", "asset_class": "stocks", "group": "Tech",
                "ticker": "MSFT", "name": "Microsoft", "currency": "USD",
                "initial_amount_eur": 300, "initial_date": "2020-01-01",
            },
            csrftoken,
        )
        assert r.status_code == 400, r.content
        detail = r.json()["detail"].lower()
        assert "nan" not in detail                     # no Decimal-crash leak
        assert "msft" in detail or "price" in detail   # helpful message
        # Atomic rollback: the Holding must not have been created.
        assert not Holding.objects.filter(ticker="MSFT").exists()

    def test_transaction_with_old_date_returns_clean_400(
        self, authed_client, csrftoken, stub_adapter
    ):
        # Holding first (no initial buy), then a transaction dated before the
        # price window -> clean 400, not a 500 / Decimal crash.
        Holding.objects.create(
            kind="portfolio", asset_class="stocks", group="Tech",
            ticker="MSFT", name="Microsoft", currency="USD",
        )
        r = _post(
            authed_client,
            "/api/transactions",
            {
                "date": "2020-01-01", "ticker": "MSFT", "action": "buy",
                "amount_eur": 100, "listing_currency": "USD",
            },
            csrftoken,
        )
        assert r.status_code == 400, r.content
        assert "nan" not in r.json()["detail"].lower()

    def test_estimate_shares_preview_old_date_returns_400(
        self, authed_client, stub_adapter
    ):
        # The GET preview endpoint must also degrade to a clean 400.
        r = authed_client.get(
            "/api/instruments/MSFT/estimate-shares"
            "?amount_eur=100&on=2020-01-01&listing_currency=USD"
        )
        assert r.status_code == 400
        assert "nan" not in r.json()["detail"].lower()

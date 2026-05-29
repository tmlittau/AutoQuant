"""Phase A: sell flow + estimate-proceeds + holding-position tests.

The schema now accepts either ``amount_eur`` OR ``shares`` on the transaction
POST. We cover:

  * a buy in EUR mode (regression for the existing path),
  * a sell in EUR mode (sign flipping),
  * a sell in units mode (the new path),
  * the validator that rejects "neither" and "both",
  * the /estimate-proceeds preview endpoint,
  * the /holdings/{ticker}/position helper.
"""

from __future__ import annotations

import json
from decimal import Decimal

import pytest

from portfolio_app.models import (
    AssetClass,
    Holding,
    HoldingKind,
    Transaction,
)


def _post(client, path, body, csrftoken):
    return client.post(
        path,
        data=json.dumps(body),
        content_type="application/json",
        HTTP_X_CSRFTOKEN=csrftoken,
    )


def _make_holding(ticker="AAPL", group="Tech", currency="USD"):
    """Create the Holding row the POST /transactions endpoint requires."""
    return Holding.objects.create(
        kind=HoldingKind.PORTFOLIO,
        asset_class=AssetClass.STOCKS,
        group=group,
        ticker=ticker,
        name=ticker,
        currency=currency,
    )


@pytest.mark.django_db
class TestTransactionEntryModes:
    def test_buy_in_eur_mode_unchanged(
        self, authed_client, csrftoken, stub_adapter
    ):
        _make_holding("AAPL")
        r = _post(
            authed_client,
            "/api/transactions",
            {
                "date": "2026-05-25",
                "ticker": "AAPL",
                "action": "buy",
                "amount_eur": 100.0,
                "listing_currency": "USD",
            },
            csrftoken,
        )
        assert r.status_code == 201, r.content
        body = r.json()
        # Buy -> positive amount, positive shares.
        assert body["amount_eur"] > 0
        assert body["shares"] > 0
        # stub adapter: price_local=105.5 by date 2026-05-25 (60 bdays from
        # 2026-04-01, but asof uses the last bday). Doesn't matter exactly --
        # just check the math is consistent (amount_eur ~= shares * price_eur).
        assert abs(body["shares"] * body["price_local"] * body["eur_per_local"]
                   - body["amount_eur"]) < 0.01

    def test_sell_in_eur_mode_flips_sign(
        self, authed_client, csrftoken, stub_adapter
    ):
        _make_holding("AAPL")
        r = _post(
            authed_client,
            "/api/transactions",
            {
                "date": "2026-05-25",
                "ticker": "AAPL",
                "action": "sell",
                "amount_eur": 100.0,
                "listing_currency": "USD",
            },
            csrftoken,
        )
        assert r.status_code == 201
        body = r.json()
        # Sell -> negative on disk for both legs.
        assert body["amount_eur"] < 0
        assert body["shares"] < 0
        assert body["action"] == "sell"

    def test_sell_in_units_mode(
        self, authed_client, csrftoken, stub_adapter
    ):
        _make_holding("AAPL")
        r = _post(
            authed_client,
            "/api/transactions",
            {
                "date": "2026-05-25",
                "ticker": "AAPL",
                "action": "sell",
                "shares": 0.5,
                "listing_currency": "USD",
            },
            csrftoken,
        )
        assert r.status_code == 201, r.content
        body = r.json()
        # shares is -0.5 (sell), amount_eur computed from EOD close * FX.
        assert body["shares"] == -0.5
        assert body["amount_eur"] < 0  # signed
        # Round-trip: |amount_eur| == |shares| * price_local * eur_per_local.
        assert abs(abs(body["amount_eur"])
                   - abs(body["shares"]) * body["price_local"] * body["eur_per_local"]
                   ) < 0.01

    def test_rejects_neither_field(
        self, authed_client, csrftoken, stub_adapter
    ):
        _make_holding("AAPL")
        r = _post(
            authed_client,
            "/api/transactions",
            {
                "date": "2026-05-25",
                "ticker": "AAPL",
                "action": "buy",
                "listing_currency": "USD",
            },
            csrftoken,
        )
        assert r.status_code == 400
        assert "exactly one" in r.json()["detail"].lower()

    def test_rejects_both_fields(
        self, authed_client, csrftoken, stub_adapter
    ):
        _make_holding("AAPL")
        r = _post(
            authed_client,
            "/api/transactions",
            {
                "date": "2026-05-25",
                "ticker": "AAPL",
                "action": "buy",
                "amount_eur": 100.0,
                "shares": 1.0,
                "listing_currency": "USD",
            },
            csrftoken,
        )
        assert r.status_code == 400


@pytest.mark.django_db
class TestEstimateProceeds:
    def test_estimate_proceeds_units_to_eur(
        self, authed_client, stub_adapter
    ):
        r = authed_client.get(
            "/api/instruments/AAPL/estimate-proceeds"
            "?shares=2.5&on=2026-05-25&listing_currency=USD"
        )
        assert r.status_code == 200
        body = r.json()
        assert body["shares"] == 2.5
        assert body["amount_eur"] > 0
        # amount_eur == shares * price_local * eur_per_local
        assert abs(body["amount_eur"]
                   - body["shares"] * body["price_local"] * body["eur_per_local"]
                   ) < 0.01

    def test_estimate_proceeds_rejects_zero_shares(
        self, authed_client, stub_adapter
    ):
        r = authed_client.get(
            "/api/instruments/AAPL/estimate-proceeds"
            "?shares=0&on=2026-05-25&listing_currency=USD"
        )
        assert r.status_code == 400


@pytest.mark.django_db
class TestHoldingPosition:
    def test_zero_position_when_no_transactions(
        self, authed_client, stub_adapter
    ):
        _make_holding("ZZZ")
        r = authed_client.get("/api/holdings/ZZZ/position")
        assert r.status_code == 200
        body = r.json()
        assert body["ticker"] == "ZZZ"
        assert body["shares"] == 0
        assert body["cost_eur"] == 0

    def test_position_sums_buys_and_sells(
        self, authed_client, csrftoken, stub_adapter
    ):
        _make_holding("AAPL")
        # Buy 100 EUR.
        _post(authed_client, "/api/transactions",
              {"date": "2026-05-01", "ticker": "AAPL", "action": "buy",
               "amount_eur": 100.0, "listing_currency": "USD"}, csrftoken)
        # Sell 0.2 shares.
        _post(authed_client, "/api/transactions",
              {"date": "2026-05-15", "ticker": "AAPL", "action": "sell",
               "shares": 0.2, "listing_currency": "USD"}, csrftoken)

        r = authed_client.get("/api/holdings/AAPL/position")
        body = r.json()
        # Net shares = (buy_shares) - 0.2; positive because buy_shares > 0.2.
        tx_buy, tx_sell = Transaction.objects.order_by("date")
        expected_shares = float(tx_buy.shares + tx_sell.shares)
        assert abs(body["shares"] - expected_shares) < 1e-6

"""Phase C: crypto swap endpoint (POST /api/transactions/swap).

A swap creates two linked Transaction rows sharing one swap_group_id:
a sell of the from-coin and a buy of the to-coin. We cover the happy
path, the shared group id, atomic rollback on a bad leg, the
same-ticker / missing-holding guards, and the default eur_value
computation.
"""

from __future__ import annotations

import json

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


def _make_coin(ticker, currency="EUR"):
    return Holding.objects.create(
        kind=HoldingKind.PORTFOLIO,
        asset_class=AssetClass.CRYPTO,
        group="Crypto",
        ticker=ticker,
        name=ticker,
        currency=currency,
    )


@pytest.mark.django_db
class TestSwap:
    def test_swap_creates_linked_pair(self, authed_client, csrftoken, stub_adapter):
        _make_coin("USDC-EUR")
        _make_coin("BTC-EUR")
        r = _post(
            authed_client,
            "/api/transactions/swap",
            {
                "date": "2026-05-25",
                "from_ticker": "USDC-EUR",
                "from_amount": 1000,
                "from_currency": "EUR",
                "to_ticker": "BTC-EUR",
                "to_amount": 0.025,
                "to_currency": "EUR",
                "eur_value": 1000,
                "note": "stack sats",
            },
            csrftoken,
        )
        assert r.status_code == 201, r.content
        body = r.json()
        # Both legs share one group id.
        assert body["swap_group_id"]
        assert body["sell"]["swap_group_id"] == body["swap_group_id"]
        assert body["buy"]["swap_group_id"] == body["swap_group_id"]

        # Sell leg: USDC out (negative shares + amount).
        assert body["sell"]["ticker"] == "USDC-EUR"
        assert body["sell"]["action"] == "sell"
        assert body["sell"]["shares"] == -1000
        assert body["sell"]["amount_eur"] == -1000

        # Buy leg: BTC in (positive shares + amount).
        assert body["buy"]["ticker"] == "BTC-EUR"
        assert body["buy"]["action"] == "buy"
        assert body["buy"]["shares"] == 0.025
        assert body["buy"]["amount_eur"] == 1000

        # Two rows persisted, both with the same UUID.
        rows = Transaction.objects.filter(swap_group_id=body["swap_group_id"])
        assert rows.count() == 2

    def test_swap_default_eur_value_from_eod(
        self, authed_client, csrftoken, stub_adapter
    ):
        _make_coin("USDC-EUR")
        _make_coin("ETH-EUR")
        # No eur_value -> computed from from_amount * from EOD price_eur.
        # stub adapter close ~ 105.x, eur_per_local for EUR == 1.0.
        r = _post(
            authed_client,
            "/api/transactions/swap",
            {
                "date": "2026-05-25",
                "from_ticker": "USDC-EUR",
                "from_amount": 2,
                "to_ticker": "ETH-EUR",
                "to_amount": 0.001,
            },
            csrftoken,
        )
        assert r.status_code == 201, r.content
        body = r.json()
        # |amount_eur| on the sell == from_amount * price_local (EUR pair, FX=1)
        sell = body["sell"]
        assert abs(abs(sell["amount_eur"]) - 2 * sell["price_local"]) < 0.01

    def test_swap_rejects_same_ticker(self, authed_client, csrftoken, stub_adapter):
        _make_coin("BTC-EUR")
        r = _post(
            authed_client,
            "/api/transactions/swap",
            {
                "date": "2026-05-25",
                "from_ticker": "BTC-EUR",
                "from_amount": 1,
                "to_ticker": "BTC-EUR",
                "to_amount": 1,
            },
            csrftoken,
        )
        assert r.status_code == 400
        assert "differ" in r.json()["detail"].lower()

    def test_swap_rejects_missing_holding(
        self, authed_client, csrftoken, stub_adapter
    ):
        _make_coin("USDC-EUR")  # only the from coin exists
        r = _post(
            authed_client,
            "/api/transactions/swap",
            {
                "date": "2026-05-25",
                "from_ticker": "USDC-EUR",
                "from_amount": 100,
                "to_ticker": "DOGE-EUR",
                "to_amount": 500,
            },
            csrftoken,
        )
        assert r.status_code == 400
        assert "DOGE-EUR" in r.json()["detail"]
        # Atomic: the from leg must NOT have been created.
        assert Transaction.objects.count() == 0

    def test_swap_rejects_nonpositive_amounts(
        self, authed_client, csrftoken, stub_adapter
    ):
        _make_coin("USDC-EUR")
        _make_coin("BTC-EUR")
        r = _post(
            authed_client,
            "/api/transactions/swap",
            {
                "date": "2026-05-25",
                "from_ticker": "USDC-EUR",
                "from_amount": 0,
                "to_ticker": "BTC-EUR",
                "to_amount": 0.025,
            },
            csrftoken,
        )
        assert r.status_code == 400

    def test_swap_position_math(self, authed_client, csrftoken, stub_adapter):
        """After a swap, from-coin net shares drop and to-coin net shares rise."""
        _make_coin("USDC-EUR")
        _make_coin("BTC-EUR")
        # Seed a USDC position first (deposit 2000 units via a buy).
        _post(authed_client, "/api/transactions",
              {"date": "2026-05-01", "ticker": "USDC-EUR", "action": "buy",
               "shares": 2000, "listing_currency": "EUR"}, csrftoken)

        _post(authed_client, "/api/transactions/swap",
              {"date": "2026-05-25", "from_ticker": "USDC-EUR", "from_amount": 1000,
               "to_ticker": "BTC-EUR", "to_amount": 0.025, "eur_value": 1000}, csrftoken)

        usdc = authed_client.get("/api/holdings/USDC-EUR/position").json()
        btc = authed_client.get("/api/holdings/BTC-EUR/position").json()
        assert abs(usdc["shares"] - 1000) < 1e-6   # 2000 deposited - 1000 swapped out
        assert abs(btc["shares"] - 0.025) < 1e-9   # 0.025 swapped in

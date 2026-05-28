"""Repository round-trip: DB rows -> dict / DataFrame shapes that the
``autoquant`` analytics expect, and the legacy CSV/YAML schema migrator."""

from __future__ import annotations

from decimal import Decimal

import pytest


@pytest.mark.django_db
class TestRepository:
    def test_empty_portfolio_dict_shape(self):
        from portfolio_app import repository as repo

        p = repo.build_portfolio_dict()
        assert p["base_currency"] == "EUR"
        assert "stocks" in p and "groups" in p["stocks"]
        assert p["etfs"] == {"holdings": []}

    def test_holding_appears_in_portfolio_dict(self):
        from portfolio_app import repository as repo
        from portfolio_app.models import GroupConfig, Holding

        GroupConfig.objects.create(
            asset_class="stocks", name="Tech", target_weight=Decimal("0.40"),
        )
        Holding.objects.create(
            kind="portfolio", asset_class="stocks", group="Tech",
            ticker="AAPL", name="Apple", currency="USD",
        )
        p = repo.build_portfolio_dict()
        assert "Tech" in p["stocks"]["groups"]
        tech = p["stocks"]["groups"]["Tech"]
        assert tech["target_weight"] == 0.40
        assert any(h["ticker"] == "AAPL" for h in tech["holdings"])

    def test_etfs_go_into_etfs_section(self):
        from portfolio_app import repository as repo
        from portfolio_app.models import Holding

        Holding.objects.create(
            kind="portfolio", asset_class="etfs", group="ETFs",
            ticker="VWCE.DE", name="Vanguard FTSE All-World", currency="EUR",
        )
        p = repo.build_portfolio_dict()
        assert len(p["etfs"]["holdings"]) == 1
        assert p["etfs"]["holdings"][0]["ticker"] == "VWCE.DE"

    def test_transactions_df_has_canonical_columns(self):
        from portfolio_app import repository as repo
        from portfolio_app.models import Transaction
        from autoquant import portfolio as pf

        Transaction.objects.create(
            date="2026-05-25", ticker="AAPL", group="Tech", action="buy",
            amount_eur=Decimal("100"), price_local=Decimal("200"),
            listing_currency="USD", eur_per_local=Decimal("0.86"),
            shares=Decimal("0.58"), fee_eur=Decimal("0"), note="seed",
        )
        df = repo.build_transactions_df()
        assert list(df.columns) == pf.TRANSACTION_COLUMNS
        assert len(df) == 1
        # Decimals are floats (pandas-friendly).
        assert isinstance(df["amount_eur"].iloc[0].item(), float)

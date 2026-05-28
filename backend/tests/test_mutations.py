"""POST/PATCH/DELETE happy paths + atomic Holding+Transaction rollback +
idempotent POST /groups + AuditEntry side-effects."""

from __future__ import annotations

import json

import pytest


def _post(client, path, body, csrftoken):
    return client.post(
        path,
        data=json.dumps(body),
        content_type="application/json",
        HTTP_X_CSRFTOKEN=csrftoken,
    )


def _patch(client, path, body, csrftoken):
    return client.patch(
        path,
        data=json.dumps(body),
        content_type="application/json",
        HTTP_X_CSRFTOKEN=csrftoken,
    )


def _delete(client, path, csrftoken):
    return client.delete(path, HTTP_X_CSRFTOKEN=csrftoken)


@pytest.mark.django_db
class TestHoldingsCreate:
    def test_watchlist_holding_create(self, authed_client, csrftoken, stub_adapter):
        r = _post(
            authed_client,
            "/api/holdings",
            {
                "kind": "watchlist", "asset_class": "stocks", "group": "Tech",
                "ticker": "NVDA", "name": "NVIDIA", "currency": "USD",
            },
            csrftoken,
        )
        assert r.status_code == 201, r.content
        assert r.json()["transaction"] is None  # no initial buy

    def test_portfolio_holding_with_initial_buy(self, authed_client, csrftoken, stub_adapter):
        r = _post(
            authed_client,
            "/api/holdings",
            {
                "kind": "portfolio", "asset_class": "stocks", "group": "Tech",
                "ticker": "MSFT", "name": "Microsoft", "currency": "USD",
                "initial_amount_eur": 100, "initial_date": "2026-05-25",
            },
            csrftoken,
        )
        assert r.status_code == 201
        body = r.json()
        assert body["ticker"] == "MSFT"
        assert body["transaction"] is not None
        assert body["transaction"]["amount_eur"] == 100.0

    def test_duplicate_holding_rejected(self, authed_client, csrftoken, stub_adapter):
        common = {
            "kind": "portfolio", "asset_class": "stocks", "group": "Tech",
            "ticker": "GOOGL", "name": "Alphabet", "currency": "USD",
        }
        assert _post(authed_client, "/api/holdings", common, csrftoken).status_code == 201
        r2 = _post(authed_client, "/api/holdings", common, csrftoken)
        assert r2.status_code == 400
        assert "already" in r2.json()["detail"].lower()

    def test_atomic_rollback_when_price_fetch_fails(self, authed_client, csrftoken):
        """If estimate_shares raises DataUnavailableError, the Holding row
        must NOT be persisted."""
        from portfolio_app.models import Holding

        before = Holding.objects.count()
        r = _post(
            authed_client,
            "/api/holdings",
            {
                "kind": "portfolio", "asset_class": "stocks", "group": "Tech",
                "ticker": "DOESNOTEXIST_XYZ", "name": "Fake", "currency": "USD",
                "initial_amount_eur": 10, "initial_date": "2026-05-25",
            },
            csrftoken,
        )
        assert r.status_code == 400
        assert Holding.objects.count() == before  # rolled back
        assert not Holding.objects.filter(ticker="DOESNOTEXIST_XYZ").exists()


@pytest.mark.django_db
class TestGroups:
    def test_post_groups_is_idempotent(self, authed_client, csrftoken):
        body = {"asset_class": "stocks", "name": "Energy", "target_weight": 0.05}
        r1 = _post(authed_client, "/api/groups", body, csrftoken)
        r2 = _post(
            authed_client, "/api/groups",
            {**body, "target_weight": 0.10}, csrftoken,
        )
        assert r1.status_code == 201
        assert r2.status_code == 201
        # Second call updates the weight, not creates a duplicate.
        from portfolio_app.models import GroupConfig
        assert GroupConfig.objects.filter(asset_class="stocks", name="Energy").count() == 1
        assert (
            float(GroupConfig.objects.get(asset_class="stocks", name="Energy").target_weight)
            == 0.10
        )


@pytest.mark.django_db
class TestTransactionsCrud:
    def test_create_patch_delete_transaction(self, authed_client, csrftoken, stub_adapter):
        # Need a holding first.
        _post(
            authed_client,
            "/api/holdings",
            {"kind": "portfolio", "asset_class": "stocks", "group": "Tech",
             "ticker": "AMZN", "name": "Amazon", "currency": "USD"},
            csrftoken,
        )
        # Create
        r = _post(
            authed_client,
            "/api/transactions",
            {"ticker": "AMZN", "date": "2026-05-25", "amount_eur": 50},
            csrftoken,
        )
        assert r.status_code == 201
        tx_id = r.json()["id"]
        # Patch
        r2 = _patch(
            authed_client,
            f"/api/transactions/{tx_id}",
            {"note": "edited", "fee_eur": 2.5},
            csrftoken,
        )
        assert r2.status_code == 200
        assert r2.json()["note"] == "edited"
        assert r2.json()["fee_eur"] == 2.5
        # Delete
        r3 = _delete(authed_client, f"/api/transactions/{tx_id}", csrftoken)
        assert r3.status_code == 204


@pytest.mark.django_db
class TestAuditTrail:
    def test_mutations_write_audit_rows(self, authed_client, csrftoken, stub_adapter):
        from portfolio_app.models import AuditEntry

        before = AuditEntry.objects.count()
        _post(
            authed_client,
            "/api/holdings",
            {"kind": "portfolio", "asset_class": "stocks", "group": "Tech",
             "ticker": "META", "name": "Meta", "currency": "USD"},
            csrftoken,
        )
        _post(
            authed_client, "/api/cache/clear", {}, csrftoken,
        )
        after = AuditEntry.objects.count()
        assert after - before >= 2
        latest = AuditEntry.objects.first()  # ordering = -timestamp
        assert latest.user == "alice"
        assert latest.method in ("POST", "DELETE", "PATCH", "PUT")

    def test_audit_redacts_secrets(self, authed_client, csrftoken):
        from portfolio_app.models import AuditEntry

        _post(
            authed_client,
            "/api/settings",
            {"av_api_key": "super-secret-12345"},
            csrftoken,
        )
        # We use PUT for settings -- adjust.
        authed_client.put(
            "/api/settings",
            data=json.dumps({"av_api_key": "super-secret-12345"}),
            content_type="application/json",
            HTTP_X_CSRFTOKEN=csrftoken,
        )
        latest = AuditEntry.objects.filter(endpoint="/settings").first()
        assert latest is not None
        # The key should be masked.
        assert "super-secret" not in json.dumps(latest.payload_diff)
        assert latest.payload_diff.get("av_api_key") == "***"

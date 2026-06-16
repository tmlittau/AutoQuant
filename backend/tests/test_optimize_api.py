"""API-level tests for the Phase R4 optimizer endpoint (POST /api/optimize)."""

from __future__ import annotations

import json

import pytest

from portfolio_app.models import AssetClass, Holding, HoldingKind


def _post(client, path, body, csrftoken):
    return client.post(
        path, data=json.dumps(body), content_type="application/json",
        HTTP_X_CSRFTOKEN=csrftoken,
    )


def _stock(ticker):
    Holding.objects.create(
        kind=HoldingKind.PORTFOLIO, asset_class=AssetClass.STOCKS,
        group="Tech", ticker=ticker, name=ticker, currency="USD",
    )


@pytest.mark.django_db
class TestOptimizeApi:
    def test_requires_at_least_two_holdings(self, authed_client, csrftoken, stub_adapter):
        _stock("AAA")
        r = _post(authed_client, "/api/optimize",
                  {"asset_class": "stocks", "method": "hrp"}, csrftoken)
        assert r.status_code == 400
        assert ">=2" in r.json()["detail"] or "2" in r.json()["detail"]

    @pytest.mark.parametrize("method", ["hrp", "min_variance", "cvar", "black_litterman"])
    def test_optimize_returns_normalised_weights(
        self, authed_client, csrftoken, stub_adapter, method
    ):
        for t in ("AAA", "BBB", "CCC"):
            _stock(t)
        r = _post(authed_client, "/api/optimize",
                  {"asset_class": "stocks", "method": method, "lookback_days": 600}, csrftoken)
        assert r.status_code == 200, r.content
        d = r.json()
        assert d["method"] == method
        total = sum(w["target_weight"] for w in d["weights"])
        assert total == pytest.approx(1.0, abs=1e-3)
        assert all(w["target_weight"] >= -1e-9 for w in d["weights"])   # long-only
        # Frontier + per-ticker points present for the chart.
        assert len(d["frontier_volatility"]) == len(d["frontier_return"])
        assert d["target_point"] is not None

    def test_max_weight_constraint_enforced(self, authed_client, csrftoken, stub_adapter):
        for t in ("AAA", "BBB", "CCC", "DDD"):
            _stock(t)
        r = _post(authed_client, "/api/optimize",
                  {"asset_class": "stocks", "method": "min_variance",
                   "max_weight": 0.4, "lookback_days": 600}, csrftoken)
        assert r.status_code == 200, r.content
        assert max(w["target_weight"] for w in r.json()["weights"]) <= 0.4 + 1e-3

    def test_invalid_method_falls_back_to_hrp(self, authed_client, csrftoken, stub_adapter):
        for t in ("AAA", "BBB"):
            _stock(t)
        r = _post(authed_client, "/api/optimize",
                  {"asset_class": "stocks", "method": "nonsense"}, csrftoken)
        assert r.status_code == 200
        assert r.json()["method"] == "hrp"

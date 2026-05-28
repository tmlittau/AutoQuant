"""Shared pytest fixtures.

Tests run against a transactional DB (pytest-django default with the
``django_db`` marker), so each test gets a clean slate. We don't hit the
real market-data adapter in unit tests -- the registry is stubbed in tests
that need it via :func:`stub_adapter`.
"""

from __future__ import annotations

import json
import pytest
from django.contrib.auth.models import User
from django.test import Client


# --------------------------------------------------------------------------- #
# Users
# --------------------------------------------------------------------------- #
@pytest.fixture
def user(db):
    """A fresh user (different per test thanks to the txn rollback)."""
    return User.objects.create_user("alice", password="alicepass")


# --------------------------------------------------------------------------- #
# Clients
# --------------------------------------------------------------------------- #
@pytest.fixture
def csrf_client(db):
    """Test client with CSRF enforcement on (mirrors prod nginx + Django)."""
    return Client(enforce_csrf_checks=True)


@pytest.fixture
def authed_client(user):
    """CSRF-enforcing client already logged in as ``alice``."""
    client = Client(enforce_csrf_checks=True)
    # Prime the csrftoken cookie.
    client.get("/api/csrf")
    csrftoken = client.cookies["csrftoken"].value
    # POST /auth/login (auth=None so no CSRF needed for login).
    r = client.post(
        "/api/auth/login",
        data=json.dumps({"username": "alice", "password": "alicepass"}),
        content_type="application/json",
    )
    assert r.status_code == 200, f"login failed: {r.content!r}"
    # Django rotates the csrftoken on login.
    return client


@pytest.fixture
def csrftoken(authed_client):
    return authed_client.cookies["csrftoken"].value


# --------------------------------------------------------------------------- #
# Adapter stub -- so tests don't hit yfinance
# --------------------------------------------------------------------------- #
@pytest.fixture
def stub_adapter(monkeypatch):
    """Replace the active adapter with a deterministic stub.

    Returns a sentinel object; tests can swap it back via the registry."""

    class _Stub:
        name = "stub"

        def get_daily(self, ticker, outputsize="compact"):
            import pandas as pd

            idx = pd.bdate_range("2026-04-01", periods=60)
            return pd.DataFrame(
                {
                    "open": [100.0] * 60,
                    "high": [101.0] * 60,
                    "low": [99.0] * 60,
                    "close": [100.0 + i * 0.1 for i in range(60)],
                    "volume": [1_000_000] * 60,
                },
                index=idx,
            )

        def get_quote(self, ticker):
            import pandas as pd

            return pd.Series(
                {
                    "price": 105.0,
                    "open": 104.0,
                    "high": 106.0,
                    "low": 103.0,
                    "previous close": 104.5,
                    "change": 0.5,
                    "change percent": 0.48,
                    "volume": 1_000_000,
                    "latest trading day": "2026-05-25",
                },
                name=ticker,
            )

        def get_fx_rate(self, frm, to):
            return 0.86 if (frm.upper(), to.upper()) == ("USD", "EUR") else 1.0

        def get_fx_daily(self, frm, to, outputsize="compact"):
            import pandas as pd

            idx = pd.bdate_range("2026-04-01", periods=60)
            return pd.DataFrame(
                {"open": [0.86] * 60, "high": [0.87] * 60,
                 "low": [0.85] * 60, "close": [0.86] * 60},
                index=idx,
            )

        def get_close_prices(self, tickers, interval="daily"):
            import pandas as pd

            tickers = list(tickers)
            idx = pd.bdate_range("2026-04-01", periods=60)
            df = pd.DataFrame(
                {t: [100.0 + i * 0.1 for i in range(60)] for t in tickers},
                index=idx,
            )
            df.index.name = "date"
            return df

        def get_history(self, ticker, interval="daily"):
            return self.get_daily(ticker)

        def get_weekly(self, ticker):
            return self.get_daily(ticker)

        def get_monthly(self, ticker):
            return self.get_daily(ticker)

        def search_symbols(self, q):
            import pandas as pd

            return pd.DataFrame(
                [{"symbol": q.upper(), "name": f"Stub {q}", "type": "Equity",
                  "region": "Test", "currency": "USD"}]
            )

        def latest_eur_per_usd(self):
            return 0.86

        def eur_per_usd_series(self):
            return self.get_fx_daily("USD", "EUR")["close"].rename("eur_per_usd")

        def to_eur(self, prices_usd, eur_per_usd):
            fx = eur_per_usd.reindex(prices_usd.index).ffill().bfill()
            return prices_usd * fx

        def resolve(self, symbol):
            return symbol

        _cache: dict = {}

    stub = _Stub()
    from portfolio_app.registry import get_registry

    reg = get_registry()
    original = reg._adapter
    reg._adapter = stub
    yield stub
    reg._adapter = original

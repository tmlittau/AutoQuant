"""Auth gates + CSRF enforcement on the public API."""

from __future__ import annotations

import json

import pytest


@pytest.mark.django_db
class TestAuthGates:
    def test_unauth_get_portfolio_returns_401(self, csrf_client):
        r = csrf_client.get("/api/portfolio?asset_class=stocks")
        assert r.status_code == 401

    def test_unauth_get_dashboard_returns_401(self, csrf_client):
        assert csrf_client.get("/api/dashboard").status_code == 401

    def test_auth_me_is_open(self, csrf_client):
        r = csrf_client.get("/api/auth/me")
        assert r.status_code == 200
        assert r.json() == {"authenticated": False, "username": None}

    def test_csrf_endpoint_sets_cookie(self, csrf_client):
        r = csrf_client.get("/api/csrf")
        assert r.status_code == 200
        assert "csrftoken" in csrf_client.cookies

    def test_login_wrong_password(self, user, csrf_client):
        r = csrf_client.post(
            "/api/auth/login",
            data=json.dumps({"username": "alice", "password": "wrong"}),
            content_type="application/json",
        )
        assert r.status_code == 401

    def test_login_success_unlocks_api(self, user, csrf_client):
        # login (auth=None, no CSRF needed)
        r = csrf_client.post(
            "/api/auth/login",
            data=json.dumps({"username": "alice", "password": "alicepass"}),
            content_type="application/json",
        )
        assert r.status_code == 200
        assert r.json()["authenticated"] is True
        # Now /portfolio works (still no holdings so positions is empty list).
        r2 = csrf_client.get("/api/portfolio?asset_class=stocks")
        assert r2.status_code == 200
        assert "positions" in r2.json()

    def test_logout_clears_session(self, authed_client, csrftoken):
        assert authed_client.get("/api/auth/me").json()["authenticated"] is True
        r = authed_client.post(
            "/api/auth/logout", HTTP_X_CSRFTOKEN=csrftoken
        )
        assert r.status_code == 200
        # Subsequent API calls 401 again.
        assert authed_client.get("/api/portfolio?asset_class=stocks").status_code == 401


@pytest.mark.django_db
class TestCsrf:
    def test_post_without_csrf_returns_403(self, authed_client):
        r = authed_client.post(
            "/api/cache/clear", content_type="application/json"
        )
        assert r.status_code == 403

    def test_post_with_csrf_works(self, authed_client, csrftoken):
        r = authed_client.post(
            "/api/cache/clear",
            content_type="application/json",
            HTTP_X_CSRFTOKEN=csrftoken,
        )
        # /cache/clear returns 200 with {adapter, at} so the SPA's top-bar
        # Refresh button can show an "Updated at" timestamp.
        assert r.status_code == 200
        body = r.json()
        assert "adapter" in body and "at" in body

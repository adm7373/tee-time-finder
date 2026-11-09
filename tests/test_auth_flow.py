"""Regression tests for the Cognito sign-in flow.

The bug: the page built its own login URL and sent ``redirect_uri={origin}/``,
while the app client only registered the API endpoint, so Cognito answered
``/error?error=redirect_mismatch``. These tests pin the invariants that keep the
two in agreement: the server owns the login URL and the redirect URI, and the
token exchange goes to the hosted UI domain, never to the app's own URL.
"""

import json

import pytest
from fastapi.testclient import TestClient

from app import main as main_module
from app.config import Settings, get_settings


def _settings(**overrides) -> Settings:
    base = dict(
        default_search_zip="02169",
        cognito_region="us-east-1",
        cognito_user_pool_id="us-east-1_ABC123",
        cognito_app_client_id="2o1mvf7bfiumaet2rpiubo2js1",
        base_url="https://abc123.execute-api.us-east-1.amazonaws.com",
        cognito_domain_url=(
            "https://tee-time-finder-550968370453.auth.us-east-1.amazoncognito.com"
        ),
        cognito_redirect_uri="",
    )
    base.update(overrides)
    return Settings(_env_file=None, **base)


@pytest.fixture
def client(monkeypatch):
    """TestClient wired to a settings object the test controls."""

    def _make(settings: Settings) -> TestClient:
        monkeypatch.setattr(main_module, "settings", settings)
        main_module.app.dependency_overrides[get_settings] = lambda: settings
        return TestClient(main_module.app)

    yield _make
    main_module.app.dependency_overrides.clear()


def test_redirect_uri_derived_from_base_url_has_trailing_slash():
    settings = _settings()
    assert (
        settings.effective_redirect_uri
        == "https://abc123.execute-api.us-east-1.amazonaws.com/"
    )


def test_explicit_redirect_uri_wins():
    settings = _settings(cognito_redirect_uri="https://example.test/callback")
    assert settings.effective_redirect_uri == "https://example.test/callback"


def test_token_url_is_the_hosted_ui_domain():
    settings = _settings()
    assert settings.cognito_token_url == (
        "https://tee-time-finder-550968370453.auth.us-east-1.amazoncognito.com"
        "/oauth2/token"
    )


def test_config_exposes_login_url_with_registered_redirect_uri(client):
    settings = _settings()
    body = client(settings).get("/config").json()

    assert body["cognito_redirect_uri"] == settings.effective_redirect_uri
    login_url = body["cognito_login_url"]
    assert login_url is not None
    assert login_url.startswith(
        "https://tee-time-finder-550968370453.auth.us-east-1.amazoncognito.com"
        "/oauth2/authorize?"
    )
    # The redirect_uri the browser will send must be the registered one.
    from urllib.parse import parse_qs, urlparse

    query = parse_qs(urlparse(login_url).query)
    assert query["redirect_uri"] == [settings.effective_redirect_uri]
    assert query["client_id"] == [settings.cognito_app_client_id]
    assert query["response_type"] == ["code"]


def test_config_without_cognito_domain_offers_no_login_url(client):
    body = client(_settings(cognito_domain_url="")).get("/config").json()
    assert body["cognito_login_url"] is None


def test_template_does_not_build_its_own_login_url():
    """The page must take the login URL from /config, not hard-code one."""
    source = main_module.templates.get_template("index.html").filename
    with open(source, encoding="utf-8") as handle:
        text = handle.read()
    assert "amazoncognito.com" not in text
    assert "authConfig.cognito_login_url" in text


def test_token_exchange_posts_to_hosted_ui_domain(client, monkeypatch):
    settings = _settings()
    captured = {}

    class FakeResponse:
        ok = True
        status_code = 200
        text = ""

        def json(self):
            return {"access_token": "token", "token_type": "Bearer"}

    def fake_post(url, data=None, headers=None, timeout=None):
        captured["url"] = url
        captured["data"] = data
        return FakeResponse()

    monkeypatch.setattr(main_module.requests, "post", fake_post)

    response = client(settings).post(
        "/auth/token",
        json={"code": "abc", "redirect_uri": "https://attacker.example/"},
    )

    assert response.status_code == 200
    assert captured["url"] == settings.cognito_token_url
    from urllib.parse import parse_qs

    body = (
        captured["data"].decode()
        if isinstance(captured["data"], bytes)
        else captured["data"]
    )
    assert parse_qs(body)["redirect_uri"] == [settings.effective_redirect_uri]


def test_token_exchange_without_code_is_a_400(client):
    response = client(_settings()).post("/auth/token", json={})
    assert response.status_code == 400
    assert response.json()["error"] == "invalid_request"


def test_token_exchange_without_domain_is_a_400(client):
    response = client(_settings(cognito_domain_url="")).post(
        "/auth/token", json={"code": "abc"}
    )
    assert response.status_code == 400
    assert response.json()["error"] == "invalid_request"


def test_health_and_root_stay_public(client):
    test_client = client(_settings())
    assert test_client.get("/health").json() == {"status": "ok"}
    assert test_client.get("/").status_code == 200


def test_debug_reports_resolved_redirect_uri(client):
    settings = _settings()
    body = client(settings).get("/debug").json()
    assert body["cognito_redirect_uri"] == settings.effective_redirect_uri
    assert json.dumps(body)  # serializable

"""Regression tests for the Cognito sign-in flow.

The original bug: the page built its own login URL and sent
``redirect_uri={origin}/`` while the app client only registered the API
endpoint, so Cognito answered ``/error?error=redirect_mismatch`` and the token
exchange failed with ``invalid_redirect`` - reported to the user as the generic
"Cognito rejected the authorization code".

These tests pin the invariants that keep the two sides in agreement: the server
owns the login URL and the redirect URI, the token exchange repeats the
registered URI rather than trusting the browser, and Cognito's own error is
passed through instead of being swallowed.
"""

from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient

from app import main as main_module
from app.config import Settings, get_settings

CLIENT_ID = "2o1mvf7bfiumaet2rpiubo2js1"
DOMAIN = "https://tee-time-finder-550968370453.auth.us-east-1.amazoncognito.com"
BASE_URL = "https://abc123.execute-api.us-east-1.amazonaws.com"


def _settings(**overrides) -> Settings:
    base = dict(
        default_search_zip="02169",
        cognito_region="us-east-1",
        cognito_user_pool_id="us-east-1_ABC123",
        cognito_app_client_id=CLIENT_ID,
        base_url=BASE_URL,
        cognito_domain_url=DOMAIN,
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


# --- settings -----------------------------------------------------------------


def test_redirect_uri_derived_from_base_url_has_trailing_slash():
    assert _settings().effective_redirect_uri == f"{BASE_URL}/"


def test_explicit_redirect_uri_wins():
    settings = _settings(cognito_redirect_uri="https://example.test/callback")
    assert settings.effective_redirect_uri == "https://example.test/callback"


def test_token_url_is_the_hosted_ui_domain():
    assert _settings().cognito_token_url == f"{DOMAIN}/oauth2/token"


def test_login_url_absent_without_configuration():
    assert _settings(cognito_domain_url="").cognito_login_url is None
    assert _settings(cognito_app_client_id="").cognito_login_url is None
    assert _settings(base_url="").cognito_login_url is None


# --- /config ------------------------------------------------------------------


def test_config_exposes_login_url_with_registered_redirect_uri(client):
    settings = _settings()
    body = client(settings).get("/config").json()

    assert body["cognito_redirect_uri"] == settings.effective_redirect_uri
    login_url = body["cognito_login_url"]
    assert login_url.startswith(f"{DOMAIN}/oauth2/authorize?")

    query = parse_qs(urlparse(login_url).query)
    assert query["redirect_uri"] == [settings.effective_redirect_uri]
    assert query["client_id"] == [CLIENT_ID]
    assert query["response_type"] == ["code"]
    assert query["scope"] == ["openid email"]


def test_config_without_cognito_domain_offers_no_login_url(client):
    body = client(_settings(cognito_domain_url="")).get("/config").json()
    assert body["cognito_login_url"] is None


def test_page_does_not_build_its_own_login_url(client):
    """The page must take the login URL from /config, never hard-code one."""
    response = client(_settings()).get("/")
    assert response.status_code == 200
    assert "amazoncognito.com" not in response.text
    assert "auth.cognito_login_url" in response.text


# --- /auth/token --------------------------------------------------------------


class _FakeResponse:
    def __init__(self, payload: dict):
        self._payload = payload
        self.status = 200

    def read(self) -> bytes:
        import json

        return json.dumps(self._payload).encode()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_token_exchange_uses_the_configured_redirect_uri(client, monkeypatch):
    captured = {}

    def fake_urlopen(request, timeout=None):
        captured["url"] = request.full_url
        captured["body"] = request.data.decode()
        return _FakeResponse({"access_token": "token", "token_type": "Bearer"})

    monkeypatch.setattr(main_module, "urlopen", fake_urlopen)

    response = client(_settings()).post(
        "/auth/token",
        json={"code": "abc", "redirect_uri": "https://attacker.example/"},
    )

    assert response.status_code == 200
    assert response.json()["access_token"] == "token"
    assert captured["url"] == f"{DOMAIN}/oauth2/token"
    # The browser-supplied redirect_uri must be ignored in favour of the
    # registered one; Cognito binds the code to the URI it was issued for.
    assert parse_qs(captured["body"])["redirect_uri"] == [f"{BASE_URL}/"]


def test_token_exchange_without_code_is_a_400(client):
    response = client(_settings()).post("/auth/token", json={})
    assert response.status_code == 400


def test_token_exchange_without_domain_is_a_400(client):
    response = client(_settings(cognito_domain_url="")).post(
        "/auth/token", json={"code": "abc"}
    )
    assert response.status_code == 400


def test_cognito_error_is_passed_through(client, monkeypatch):
    """A real Cognito failure must not be flattened into a generic message."""
    import io
    from urllib.error import HTTPError

    body = b'{"error":"invalid_grant","error_description":"Invalid grant provided"}'

    def fake_urlopen(request, timeout=None):
        raise HTTPError(request.full_url, 400, "Bad Request", {}, io.BytesIO(body))

    monkeypatch.setattr(main_module, "urlopen", fake_urlopen)

    response = client(_settings()).post("/auth/token", json={"code": "abc"})

    assert response.status_code == 400
    detail = response.json()["detail"]
    assert detail["error"] == "invalid_grant"
    assert detail["error_description"] == "Invalid grant provided"


# --- public surface -----------------------------------------------------------


def test_health_and_root_stay_public(client):
    test_client = client(_settings())
    assert test_client.get("/health").json() == {"status": "ok"}
    assert test_client.get("/").status_code == 200

"""Tests for the /search authentication gate.

Two layers guard /search:

- API Gateway's JWT authorizer validates signature, issuer and expiry. Its
  ``audience`` list is empty on purpose: Cognito ACCESS tokens carry
  ``client_id`` and no ``aud``, so an audience check there would reject the very
  token the app sends.
- ``app.auth.require_user`` then verifies the token belongs to our app client.

These tests pin the second layer, which is now the only one checking the client.
"""

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app import auth as auth_module
from app import main as main_module
from app.config import Settings, get_settings

CLIENT_ID = "2o1mvf7bfiumaet2rpiubo2js1"


def _settings(**overrides) -> Settings:
    base = dict(
        default_search_zip="02169",
        cognito_region="us-east-1",
        cognito_user_pool_id="us-east-1_ABC123",
        cognito_app_client_id=CLIENT_ID,
        base_url="https://abc123.execute-api.us-east-1.amazonaws.com",
        cognito_domain_url="",
        cognito_redirect_uri="",
    )
    base.update(overrides)
    return Settings(_env_file=None, **base)


def _claims(**overrides) -> dict:
    base = {
        "token_use": "access",
        "client_id": CLIENT_ID,
        "sub": "user-1",
        "exp": int((datetime.now(timezone.utc) + timedelta(hours=1)).timestamp()),
    }
    base.update(overrides)
    return base


@pytest.fixture
def client(monkeypatch):
    """TestClient whose JWT verification is stubbed, so no JWKS fetch happens.

    ``client.state["claims"]`` is whatever the test wants the signed token to
    contain; the signature itself is not what is under test here.
    """
    state = {"claims": {}, "error": None}

    def fake_get_unverified_header(token):
        return {"kid": "test-key"}

    def fake_decode(token, key, algorithms=None, issuer=None, options=None):
        if state["error"] is not None:
            raise state["error"]
        return state["claims"]

    monkeypatch.setattr(
        auth_module.jwt, "get_unverified_header", fake_get_unverified_header
    )
    monkeypatch.setattr(auth_module.jwt, "decode", fake_decode)
    monkeypatch.setattr(
        auth_module, "get_jwks", lambda issuer: {"keys": [{"kid": "test-key"}]}
    )

    def _make() -> TestClient:
        settings = _settings()
        monkeypatch.setattr(main_module, "settings", settings)
        main_module.app.dependency_overrides[get_settings] = lambda: settings
        return TestClient(main_module.app)

    _make.state = state
    yield _make
    main_module.app.dependency_overrides.clear()


def _search(test_client: TestClient, token: str = "token"):
    return test_client.post(
        "/search",
        json={"date": "2026-11-10", "players": 2},
        headers={"authorization": f"Bearer {token}"},
    )


def test_access_token_with_matching_client_id_is_accepted(client, monkeypatch):
    monkeypatch.setattr(
        main_module, "search_all", lambda request, store=None, geocoder=None: []
    )
    test_client = client()
    client.state["claims"] = _claims()
    response = _search(test_client)
    assert response.status_code == 200
    assert response.json() == []


def test_access_token_for_another_app_client_is_rejected(client, monkeypatch):
    monkeypatch.setattr(
        main_module, "search_all", lambda request, store=None, geocoder=None: []
    )
    test_client = client()
    client.state["claims"] = _claims(client_id="some-other-client")
    assert _search(test_client).status_code == 401


def test_id_token_with_matching_aud_is_accepted(client, monkeypatch):
    monkeypatch.setattr(
        main_module, "search_all", lambda request, store=None, geocoder=None: []
    )
    test_client = client()
    client.state["claims"] = _claims(token_use="id", aud=CLIENT_ID)
    assert _search(test_client).status_code == 200


def test_id_token_for_another_app_client_is_rejected(client, monkeypatch):
    monkeypatch.setattr(
        main_module, "search_all", lambda request, store=None, geocoder=None: []
    )
    test_client = client()
    claims = _claims(token_use="id", aud="some-other-client")
    claims.pop("client_id")
    client.state["claims"] = claims
    assert _search(test_client).status_code == 401


def test_token_without_client_claim_is_rejected(client, monkeypatch):
    monkeypatch.setattr(
        main_module, "search_all", lambda request, store=None, geocoder=None: []
    )
    test_client = client()
    claims = _claims()
    claims.pop("client_id")
    client.state["claims"] = claims
    assert _search(test_client).status_code == 401


def test_unknown_token_use_is_rejected(client):
    test_client = client()
    client.state["claims"] = _claims(token_use="refresh")
    assert _search(test_client).status_code == 401


def test_missing_bearer_token_is_rejected(client):
    test_client = client()
    response = test_client.post("/search", json={"date": "2026-11-10", "players": 2})
    assert response.status_code == 401


def test_invalid_signature_is_rejected(client):
    test_client = client()
    client.state["error"] = auth_module.JWTError("bad signature")
    assert _search(test_client).status_code == 401

"""Admin routes: group membership is the permission, and triggering is async."""

import json

import pytest
from fastapi.testclient import TestClient

from app import admin
from app.config import get_settings
from app.main import app


@pytest.fixture()
def client(monkeypatch):
    """A client whose requests are already 'signed in' as an admin."""
    from app.auth import require_user

    claims = {
        "sub": "test",
        "cognito:groups": ["admins"],
        "token_use": "access",
    }
    app.dependency_overrides[require_user] = lambda: claims
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_is_admin_reads_the_group_claim():
    assert admin.is_admin({"cognito:groups": ["admins"]})
    assert not admin.is_admin({"cognito:groups": ["other"]})
    # A token without the scope carries no claim at all: that is a "no", not an
    # error, or a missing claim would 500 instead of 403.
    assert not admin.is_admin({})


def test_non_admin_gets_403(monkeypatch):
    from app.auth import require_user

    app.dependency_overrides[require_user] = lambda: {"cognito:groups": ["users"]}
    try:
        response = TestClient(app).post("/admin/discover")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 403
    assert "admins" in response.json()["detail"]


def test_admin_page_hides_the_section_from_non_admins():
    from app.auth import require_user

    app.dependency_overrides[require_user] = lambda: {"cognito:groups": []}
    try:
        body = TestClient(app).get("/admin").json()
    finally:
        app.dependency_overrides.clear()

    assert body["admin"] is False
    # The steps are still listed so the page can describe what admins can do.
    assert {step["id"] for step in body["steps"]} == {"discover", "fingerprint"}


def test_trigger_invokes_asynchronously(client, monkeypatch):
    calls = []

    class FakeLambda:
        def invoke(self, **kwargs):
            calls.append(kwargs)
            return {"StatusCode": 202}

    monkeypatch.setattr(admin.boto3, "client", lambda *a, **k: FakeLambda())

    response = client.post("/admin/discover")

    assert response.status_code == 200
    assert response.json()["started"] is True
    assert len(calls) == 1
    # InvocationType="Event" is what keeps the worker's minutes out of the
    # API's 30-second timeout.
    assert calls[0]["InvocationType"] == "Event"
    assert calls[0]["FunctionName"].endswith("-discover")
    assert json.loads(calls[0]["Payload"])["force_sweep"] is True


def test_trigger_ignores_payload_from_the_browser(client, monkeypatch):
    calls = []

    class FakeLambda:
        def invoke(self, **kwargs):
            calls.append(kwargs)
            return {"StatusCode": 202}

    monkeypatch.setattr(admin.boto3, "client", lambda *a, **k: FakeLambda())

    # The route takes no body: a caller cannot widen the run's cost.
    client.post("/admin/fingerprint", json={"limit": 10000})

    assert json.loads(calls[0]["Payload"])["limit"] == 10


def test_unknown_step_is_404(client):
    assert client.post("/admin/nonsense").status_code == 404


def test_worker_failure_is_502(client, monkeypatch):
    class Broken:
        def invoke(self, **kwargs):
            raise RuntimeError("no such function")

    monkeypatch.setattr(admin.boto3, "client", lambda *a, **k: Broken())

    response = client.post("/admin/discover")

    assert response.status_code == 502
    assert "no such function" in response.json()["detail"]


def test_function_names_follow_the_project_name(monkeypatch):
    monkeypatch.setenv("PROJECT_NAME", "custom")
    get_settings.cache_clear()
    try:
        assert admin._function_name("discover") == "custom-discover"
    finally:
        get_settings.cache_clear()

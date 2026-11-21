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
    assert {step["id"] for step in body["steps"]} == {
        "discover",
        "fingerprint",
    }


def test_seed_step_is_gone(client):
    """Canonical courses are rows now; nothing in code to re-seed them from."""
    assert client.post("/admin/seed").status_code == 404


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


class _InMemoryCourseStore:
    def __init__(self, items=None):
        self._items = {item["pk"]: dict(item) for item in (items or [])}

    def scan(self):
        return list(self._items.values())

    def meta(self, key):
        return self._items.get(f"meta#{key}")

    def put_meta(self, key, value):
        self._items[f"meta#{key}"] = {"pk": f"meta#{key}", "value": value}

    def put(self, item):
        self._items[item["pk"]] = dict(item)

    def delete(self, slug):
        pk = slug if slug.startswith("course#") else f"course#{slug}"
        if pk in self._items:
            del self._items[pk]
            return True
        return True


def test_list_courses_requires_admin():
    from app.auth import require_user

    app.dependency_overrides[require_user] = lambda: {"cognito:groups": ["users"]}
    try:
        response = TestClient(app).get("/admin/courses")
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 403


def test_list_courses_returns_metrics_and_courses(client, monkeypatch):
    store = _InMemoryCourseStore(
        [
            {
                "pk": "course#presidents",
                "name": "Presidents Golf Course",
                "provider": "teeitup",
                "distance_from_zip": 2.5,
            },
            {
                "pk": "course#unknown-course",
                "name": "Unknown Golf",
                "provider": "unknown",
                "distance_from_zip": 10.0,
            },
            {
                "pk": "meta#last_sweep",
                "value": "2026-11-20T12:00:00Z",
            },
        ]
    )
    monkeypatch.setattr("app.main._course_store", lambda: store)

    response = client.get("/admin/courses")
    assert response.status_code == 200
    data = response.json()
    assert data["metrics"]["total"] == 2
    assert data["metrics"]["teeitup"] == 1
    assert data["metrics"]["unknown"] == 1
    assert data["metrics"]["last_sweep"] == "2026-11-20T12:00:00Z"
    assert len(data["courses"]) == 2
    assert data["courses"][0]["name"] == "Presidents Golf Course"
    # The row is labelled teeitup but carries no id, so search skips it.
    assert data["metrics"]["searchable"] == 0
    assert data["courses"][0]["searchable"] is False
    assert "ids the teeitup adapter needs" in data["courses"][0]["searchable_reason"]


def test_delete_course_requires_admin():
    from app.auth import require_user

    app.dependency_overrides[require_user] = lambda: {"cognito:groups": ["users"]}
    try:
        response = TestClient(app).delete("/admin/courses/presidents")
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 403


def test_delete_course_removes_from_store(client, monkeypatch):
    store = _InMemoryCourseStore(
        [
            {
                "pk": "course#presidents",
                "name": "Presidents Golf Course",
                "provider": "teeitup",
            }
        ]
    )
    monkeypatch.setattr("app.main._course_store", lambda: store)

    response = client.delete("/admin/courses/presidents")
    assert response.status_code == 200
    assert response.json()["deleted"] is True
    assert "course#presidents" not in store._items


@pytest.mark.parametrize(
    "row, searchable, reason",
    [
        (
            {
                "pk": "course#ready",
                "provider": "teeitup-confirmed",
                "facility_id": "5720",
                "x_be_alias": "norwood-country-club",
            },
            True,
            "",
        ),
        (
            {
                "pk": "course#foreup",
                "provider": "foreup",
                "foreup_course_id": "19865",
                "foreup_schedule_id": "2748",
            },
            True,
            "",
        ),
        (
            {"pk": "course#noids", "provider": "foreup", "foreup_course_id": "19969"},
            False,
            "ids the foreup adapter needs",
        ),
        (
            {"pk": "course#unknown", "provider": "unknown"},
            False,
            "not classified",
        ),
        (
            {"pk": "course#members", "provider": "members-only"},
            False,
            "terminal verdict",
        ),
        (
            {"pk": "course#cps", "provider": "cps-golf", "cps_site": "peabody"},
            False,
            "no adapter for cps-golf",
        ),
    ],
)
def test_admin_says_whether_a_course_is_searchable(
    client, monkeypatch, row, searchable, reason
):
    """The badge mirrors the search catalog, so green means a search uses the row."""
    store = _InMemoryCourseStore([row, {"pk": "meta#last_sweep", "value": "x"}])
    monkeypatch.setattr("app.main._course_store", lambda: store)

    data = client.get("/admin/courses").json()

    course = data["courses"][0]
    assert course["searchable"] is searchable
    assert reason in course["searchable_reason"]
    assert data["metrics"]["searchable"] == (1 if searchable else 0)

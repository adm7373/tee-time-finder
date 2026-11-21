from datetime import date
from functools import lru_cache
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from urllib.parse import urlencode
from urllib.request import HTTPError, Request, urlopen
import json

from .admin import STEPS, is_admin, require_admin, trigger
from .auth import require_user
from .config import get_settings
from .discovery import CourseStore
from .geo import GeocodeStore, Geocoder
from .models import SearchRequest, TeeTimeOption
from .providers import (
    ENGINE_PROVIDERS,
    TERMINAL_LABELS,
    provider_for_row,
    search_all,
)
from mangum import Mangum

settings = get_settings()
app = FastAPI(title="Tee Time Finder", version="0.1.0")

_INDEX_HTML_PATH = Path(__file__).parent / "static" / "index.html"

# One store and one geocoder per process: the store memoises coordinates it has
# already read, so a warm Lambda avoids DynamoDB entirely on repeat searches.
_geocode_store = lru_cache(maxsize=1)(lambda: GeocodeStore(settings.geocode_table_name))
_course_store = lru_cache(maxsize=1)(lambda: CourseStore(settings.courses_table_name))
_geocoder = lru_cache(maxsize=1)(lambda: Geocoder(settings.nominatim_user_agent))


@app.get("/", response_class=HTMLResponse)
def page() -> str:
    return _INDEX_HTML_PATH.read_text(encoding="utf-8")


@app.post("/auth/token")
def exchange_code(payload: dict[str, str]) -> dict[str, object]:
    """Exchange a Cognito authorization code without storing credentials in the browser.

    The redirect URI is taken from configuration rather than from the request:
    Cognito binds the code to the URI it was issued for, so the exchange must
    repeat that exact value.
    """
    client_id = settings.cognito_app_client_id
    token_url = settings.cognito_token_url
    redirect_uri = settings.effective_redirect_uri
    if not (client_id and token_url and redirect_uri and payload.get("code")):
        raise HTTPException(status_code=400, detail={"error": "invalid_request"})
    body = urlencode(
        {
            "grant_type": "authorization_code",
            "client_id": client_id,
            "code": payload["code"],
            "redirect_uri": redirect_uri,
        }
    ).encode()
    request = Request(
        token_url,
        data=body,
        headers={"content-type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=10) as response:
            return json.loads(response.read())
    except HTTPError as error:
        # Surface Cognito's own error rather than a generic message: the
        # difference between invalid_grant and invalid_redirect is the whole
        # diagnosis when sign-in fails.
        try:
            details = json.loads(error.read())
        except ValueError:
            details = {}
        raise HTTPException(
            status_code=error.code,
            detail={
                "error": details.get("error", "token_exchange_failed"),
                "error_description": details.get(
                    "error_description", "Cognito rejected the authorization code"
                ),
            },
        )


@app.post("/admin/{step}")
def run_step(
    step: str, user: dict = Depends(require_admin)
) -> dict[str, object]:
    """Start one worker and return immediately.

    Admin-only: membership of the Cognito ``admins`` group is the permission.
    The work runs in the worker Lambda under its own ten-minute timeout, so a
    slow sweep never holds this request open.
    """
    return trigger(step)


def _not_searchable_reason(label: str, engine) -> str:
    """Why a row is not searchable, in the terms an admin can act on."""
    if engine is None:
        if label in ("", "unknown"):
            return "not classified: no booking engine identified yet"
        if label in TERMINAL_LABELS:
            return f"terminal verdict: {label} is never searched"
        return f"no adapter for {label} yet"
    return f"row is missing the ids the {label} adapter needs"


@app.get("/admin/courses")
def list_courses(user: dict = Depends(require_admin)) -> dict[str, object]:
    """List all courses in DynamoDB with metrics. Admin-only.

    Each course carries ``searchable`` and, when it is not, the reason: the
    admin page uses the same ``provider_for_row`` the search catalog does, so a
    green badge means a non-admin search would actually use the row.

    Hidden rows are still listed, flagged, so they can be unhidden, but they are
    left out of the metrics: hiding means the course stops counting as one the
    app should be searching or classifying.
    """
    store = _course_store()
    rows = [r for r in store.scan() if r.get("pk", "").startswith("course#")]

    by_provider: dict[str, int] = {}
    searchable_count = 0
    courses = []
    for row in rows:
        item = dict(row)
        if "distance_from_zip" in item and hasattr(item["distance_from_zip"], "__float__"):
            item["distance_from_zip"] = float(item["distance_from_zip"])
        label = item.get("provider") or "unknown"
        engine = ENGINE_PROVIDERS.get(label)
        item["hidden"] = bool(item.get("hidden"))
        try:
            provider = None if item["hidden"] else provider_for_row(item)
        except Exception:  # a broken row must not break the whole page
            provider = None
        item["searchable"] = provider is not None
        if item["hidden"]:
            item["searchable_reason"] = "hidden"
            courses.append(item)
            continue
        item["searchable_reason"] = (
            "" if provider is not None else _not_searchable_reason(label, engine)
        )
        by_provider[label] = by_provider.get(label, 0) + 1
        if provider is not None:
            searchable_count += 1
        courses.append(item)

    courses.sort(
        key=lambda x: (
            x["hidden"],
            x.get("distance_from_zip") if x.get("distance_from_zip") is not None else 999,
            x.get("name", ""),
        )
    )

    last_sweep_meta = store.meta("last_sweep")

    return {
        "metrics": {
            "total": sum(by_provider.values()),
            "teeitup": by_provider.get("teeitup", 0) + by_provider.get("teeitup-confirmed", 0),
            "other": sum(
                v for k, v in by_provider.items() if k not in ("teeitup", "teeitup-confirmed", "unknown")
            ),
            "unknown": by_provider.get("unknown", 0),
            "searchable": searchable_count,
            "by_provider": by_provider,
            "last_sweep": last_sweep_meta.get("value") if last_sweep_meta else None,
        },
        "courses": courses,
    }


class CourseUpdate(BaseModel):
    """The row fields an admin may change from the page."""

    hidden: bool


@app.patch("/admin/courses/{slug}")
def update_course(
    slug: str, body: CourseUpdate, user: dict = Depends(require_admin)
) -> dict[str, object]:
    """Hide or unhide a course. Admin-only.

    Hiding keeps the row, which is what stops the next discovery sweep from
    recreating the place; a deleted row comes back.
    """
    if not _course_store().set_hidden(slug, body.hidden):
        raise HTTPException(status_code=500, detail="Failed to update course")
    return {"hidden": body.hidden, "slug": slug}


@app.delete("/admin/courses/{slug}")
def delete_course(slug: str, user: dict = Depends(require_admin)) -> dict[str, object]:
    """Delete a course from DynamoDB by slug. Admin-only."""
    store = _course_store()
    deleted = store.delete(slug)
    if not deleted:
        raise HTTPException(status_code=500, detail="Failed to delete course")
    return {"deleted": True, "slug": slug}


@app.get("/admin")
def admin_page(user: dict = Depends(require_user)) -> dict[str, object]:
    """What the admin page may offer this user.

    Non-admins get the page with ``admin: false`` rather than a 403, so the
    signed-in app can hide the section instead of showing a broken button.
    """
    return {
        "admin": is_admin(user),
        "steps": [
            {"id": key, "label": value["label"], "detail": value["detail"]}
            for key, value in STEPS.items()
        ],
    }


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/config")
def public_config() -> dict[str, str | None]:
    """Non-sensitive values the page needs: defaults and the Cognito login URL.

    Everything here is already visible to any browser that loads the page; no
    secret is exposed.
    """
    return {
        "default_search_zip": settings.default_search_zip,
        "default_date": date.today().isoformat(),
        "cognito_login_url": settings.cognito_login_url,
        "cognito_redirect_uri": settings.effective_redirect_uri,
    }


@app.post("/search", response_model=list[TeeTimeOption])
def search(
    request: SearchRequest, user: dict = Depends(require_user)
) -> list[TeeTimeOption]:
    """Search all configured providers.

    Guarded by ``require_user`` alone. The API Gateway JWT authorizer checks the
    token's signature, issuer and expiry but not its audience: Cognito access
    tokens carry ``client_id`` rather than ``aud``, so the app client is
    verified here instead.
    """
    return search_all(
        request, _geocode_store(), _geocoder(), course_store=_course_store()
    )


handler = Mangum(app)

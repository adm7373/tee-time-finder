from fastapi import Depends, FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from urllib.parse import urlencode
from urllib.request import HTTPError, urlopen
import json
from datetime import date

from .auth import require_user
from .config import get_settings
from .models import SearchRequest, TeeTimeOption
from .providers import search_all
from mangum import Mangum

settings = get_settings()
app = FastAPI(title="Tee Time Finder", version="0.1.0")

templates = Jinja2Templates(directory="templates")


@app.get("/", response_class=HTMLResponse)
def page(request: Request) -> str:
    return templates.TemplateResponse(
        request, "index.html", {"date": date.today()}
    )  # noqa: E501


@app.post("/auth/token")
def exchange_code(payload: dict[str, str]) -> dict[str, object]:
    """Exchange a Cognito authorization code without storing credentials in the browser."""  # noqa: E501
    domain = settings.base_url
    client_id = settings.cognito_app_client_id
    if (
        not client_id or not payload.get("code") or not payload.get("redirect_uri")
    ):  # noqa: E501
        return {"error": "invalid_request"}
    body = urlencode(
        {
            "grant_type": "authorization_code",
            "client_id": client_id,
            "code": payload["code"],
            "redirect_uri": payload["redirect_uri"],
        }
    ).encode()
    request = Request(
        f"https://{domain}/oauth2/token",
        data=body,
        headers={"content-type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=10) as response:
            return json.loads(response.read())
    except HTTPError as error:
        details = json.loads(error.read())
        return {
            "error": details.get("error", "token_exchange_failed"),
            "error_description": details.get(
                "error_description", "Cognito rejected the authorization code"
            ),
        }


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/config")
def public_config() -> dict[str, str]:
    """Return non-sensitive defaults needed by the search form."""
    return {
        "default_search_zip": settings.default_search_zip,
        "default_date": date.today(),
    }


@app.post("/search", response_model=list[TeeTimeOption])
def search(
    request: SearchRequest, user: dict = Depends(require_user)
) -> list[TeeTimeOption]:
    """Search all configured providers"""
    return search_all(request)


handler = Mangum(app)

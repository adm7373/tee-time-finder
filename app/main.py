import os
from fastapi import Depends, FastAPI, Request
from fastapi.logger import logger
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from urllib.parse import urlencode
import requests
from datetime import date

from .auth import require_user
from .config import get_settings
from .models import SearchRequest, TeeTimeOption
from .providers import search_all
from mangum import Mangum

settings = get_settings()
app = FastAPI(title="Tee Time Finder", version="0.1.0")

templates = Jinja2Templates(
    directory=os.path.join(os.path.dirname(os.path.abspath(__file__)), "templates")
)


@app.get("/", response_class=HTMLResponse)
def page(request: Request) -> str:
    return templates.TemplateResponse(
        request, "index.html", {"date": date.today()}
    )  # noqa: E501


@app.post("/auth/token")
def exchange_code(payload: dict[str, str]) -> dict[str, object]:
    """Exchange a Cognito authorization code without storing creds in the browser."""
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

    try:
        logger.info(
            "Exchanging code.",
            client_id={settings.cognito_app_client_id},
            base_url={settings.base_url},
        )
        response = requests.post(
            f"{settings.base_url}/oauth2/token",
            data=body,
            headers={"content-type": "application/x-www-form-urlencoded"},
            timeout=10,
        )
        if not response.ok:
            try:
                error_details = response.json()
            except ValueError:
                error_details = {
                    "error": "invalid_response",
                    "error_description": response.text,
                }
            error_msg = (
                f"Cognito token exchange failed: "
                f"status={response.status_code}, "
                f"client_id={settings.cognito_app_client_id}, "
                f"error={error_details.get('error')}, "
                f"error_description={error_details.get('error_description')}, "
                f"response_text={response.text[:500]}"
            )
            logger.error(error_msg)
            return {
                "error": "token_exchange_failed",
                "error_description": "Cognito rejected the authorization code",
            }
        return response.json()
    except requests.RequestException as error:
        logger.error(f"Connection error exchanging code: {error}")
        return {
            "error": "token_exchange_failed",
            "error_description": "Cognito rejected the authorization code",
        }


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/debug")
def debug() -> dict[str, str]:
    return {
        "base_url": settings.base_url,
        "cognito_issuer": settings.cognito_issuer,
        "cognito_app_client_id": settings.cognito_app_client_id,
    }


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

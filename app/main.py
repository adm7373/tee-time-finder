import os
from fastapi import Depends, FastAPI, Request
from fastapi.logger import logger
from fastapi.responses import HTMLResponse, JSONResponse
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
    # The redirect_uri is bound to the code by Cognito, so it must be the one
    # registered on the app client rather than whatever the browser sent.
    redirect_uri = settings.effective_redirect_uri
    if (
        not client_id
        or not settings.cognito_token_url
        or not payload.get("code")
        or not redirect_uri
    ):  # noqa: E501
        return JSONResponse(status_code=400, content={"error": "invalid_request"})
    body = urlencode(
        {
            "grant_type": "authorization_code",
            "client_id": client_id,
            "code": payload["code"],
            "redirect_uri": redirect_uri,
        }
    ).encode()

    try:
        logger.info(
            "Exchanging code. client_id=%s token_url=%s redirect_uri=%s",
            client_id,
            settings.cognito_token_url,
            redirect_uri,
        )
        response = requests.post(
            settings.cognito_token_url,
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
def debug() -> dict[str, str | None]:
    return {
        "base_url": settings.base_url,
        "cognito_issuer": settings.cognito_issuer,
        "cognito_app_client_id": settings.cognito_app_client_id,
        "cognito_domain_url": settings.cognito_domain_url,
        "cognito_redirect_uri": settings.effective_redirect_uri,
    }


@app.get("/config")
def public_config() -> dict[str, str | None]:
    """Return non-sensitive defaults needed by the search form.

    Includes the Cognito hosted-UI login URL and the redirect URI the app client
    has registered, so the browser never has to guess either one. Guessing the
    redirect URI is what produced ``error=redirect_mismatch``: the page sent
    ``{origin}/`` while the app client only allows the API endpoint.
    """
    redirect_uri = settings.effective_redirect_uri
    login_url = None
    if settings.cognito_domain_url and settings.cognito_app_client_id and redirect_uri:
        login_url = (
            f"{settings.cognito_domain_url.rstrip('/')}/oauth2/authorize?"
            + urlencode(
                {
                    "client_id": settings.cognito_app_client_id,
                    "response_type": "code",
                    "scope": "openid email",
                    "redirect_uri": redirect_uri,
                }
            )
        )
    return {
        "default_search_zip": settings.default_search_zip,
        "default_date": date.today().isoformat(),
        "cognito_login_url": login_url,
        "cognito_redirect_uri": redirect_uri,
    }


@app.post("/search", response_model=list[TeeTimeOption])
def search(
    request: SearchRequest, user: dict = Depends(require_user)
) -> list[TeeTimeOption]:
    """Search all configured providers"""
    return search_all(request)


handler = Mangum(app)

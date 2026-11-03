from functools import lru_cache
from urllib.request import urlopen
import json

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt

from .config import Settings, get_settings

bearer = HTTPBearer(auto_error=False)


@lru_cache(maxsize=8)
def get_jwks(issuer: str) -> dict:
    with urlopen(f"{issuer}/.well-known/jwks.json", timeout=5) as response:
        return json.load(response)


def _unauthorized(detail: str = "Invalid or missing Cognito access token") -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )


def require_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
    settings: Settings = Depends(get_settings),
) -> dict:
    if not credentials or credentials.scheme.lower() != "bearer":
        raise _unauthorized()
    if not settings.cognito_issuer or not settings.cognito_app_client_id:
        raise HTTPException(status_code=503, detail="Cognito authentication is not configured")

    try:
        token = credentials.credentials
        header = jwt.get_unverified_header(token)
        key = next(key for key in get_jwks(settings.cognito_issuer)["keys"] if key["kid"] == header["kid"])
        claims = jwt.decode(
            token,
            key,
            algorithms=["RS256"],
            issuer=settings.cognito_issuer,
            options={"verify_aud": False},
        )
    except (JWTError, KeyError, StopIteration, ValueError, TimeoutError, OSError):
        raise _unauthorized()

    token_use = claims.get("token_use")
    if token_use == "access" and claims.get("client_id") == settings.cognito_app_client_id:
        return claims
    if token_use == "id" and claims.get("aud") == settings.cognito_app_client_id:
        return claims
    if token_use not in {"access", "id"}:
        raise _unauthorized("Cognito ID or access token required")
    if claims.get("client_id") != settings.cognito_app_client_id and claims.get("aud") != settings.cognito_app_client_id:
        raise _unauthorized()
    return claims


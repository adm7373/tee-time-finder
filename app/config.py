from functools import lru_cache
from urllib.parse import urlencode

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    default_search_zip: str = "02169"
    default_radius: float = 25.0
    cognito_region: str = "us-east-1"
    cognito_user_pool_id: str | None = None
    cognito_app_client_id: str | None = None

    #: Public URL the app is served from, e.g. the HTTP API endpoint.
    base_url: str = ""
    #: Cognito hosted-UI domain, e.g.
    #: https://tee-time-finder-123456789012.auth.us-east-1.amazoncognito.com
    cognito_domain_url: str = ""
    #: OAuth callback registered on the app client. It must match one of the app
    #: client's ``callback_urls`` exactly, or Cognito answers the authorize
    #: request with ``error=redirect_mismatch``.
    cognito_redirect_uri: str = ""

    #: DynamoDB table holding cached ZIP and course coordinates.
    geocode_table_name: str | None = None
    #: DynamoDB table where discovered courses and booking fingerprints live.
    courses_table_name: str | None = None
    #: Identifies this app to Nominatim, whose usage policy requires it.
    nominatim_user_agent: str = "tee-time-finder/0.1 (personal tool)"

    @property
    def cognito_issuer(self) -> str | None:
        if not self.cognito_user_pool_id:
            return None
        return (
            f"https://cognito-idp.{self.cognito_region}.amazonaws.com/"
            f"{self.cognito_user_pool_id}"
        )

    @property
    def cognito_token_url(self) -> str | None:
        """Token endpoint, which lives on the hosted UI domain."""
        if not self.cognito_domain_url:
            return None
        return f"{self.cognito_domain_url.rstrip('/')}/oauth2/token"

    @property
    def effective_redirect_uri(self) -> str | None:
        """Explicit redirect URI if set, else derived from ``base_url``."""
        if self.cognito_redirect_uri:
            return self.cognito_redirect_uri
        if self.base_url:
            return f"{self.base_url.rstrip('/')}/"
        return None

    @property
    def cognito_login_url(self) -> str | None:
        """Hosted-UI authorize URL, or None when sign-in is not configured.

        The server builds this so the browser never has to guess the redirect
        URI; a guessed one is what produces ``error=redirect_mismatch``.
        """
        redirect_uri = self.effective_redirect_uri
        if not (
            self.cognito_domain_url and self.cognito_app_client_id and redirect_uri
        ):
            return None
        query = urlencode(
            {
                "client_id": self.cognito_app_client_id,
                "response_type": "code",
                "scope": "openid email",
                "redirect_uri": redirect_uri,
            }
        )
        return f"{self.cognito_domain_url.rstrip('/')}/oauth2/authorize?{query}"

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


@lru_cache
def get_settings() -> Settings:
    return Settings()

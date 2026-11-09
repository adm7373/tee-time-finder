from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    default_search_zip: str = "02169"
    cognito_region: str = "us-east-1"
    cognito_user_pool_id: str | None = None
    cognito_app_client_id: str | None = None

    #: Public URL the app is served from (e.g. the HTTP API endpoint).
    base_url: str = ""
    #: Public URL of the Cognito hosted UI, e.g.
    #: https://tee-time-finder-123456789012.auth.us-east-1.amazoncognito.com
    cognito_domain_url: str = ""
    #: OAuth callback registered on the Cognito app client. It must match one of
    #: the app client's ``callback_urls`` exactly or Cognito answers the
    #: authorize request with ``error=redirect_mismatch``.
    cognito_redirect_uri: str = ""

    @property
    def cognito_issuer(self) -> str | None:
        if not self.cognito_user_pool_id:
            return None
        return f"https://cognito-idp.{self.cognito_region}.amazonaws.com/{self.cognito_user_pool_id}"  # noqa: E501

    @property
    def cognito_token_url(self) -> str | None:
        """Token endpoint on the Cognito hosted UI domain."""
        if not self.cognito_domain_url:
            return None
        return f"{self.cognito_domain_url.rstrip('/')}/oauth2/token"

    @property
    def effective_redirect_uri(self) -> str | None:
        """Redirect URI to send to Cognito: explicit, else derived from base_url."""
        if self.cognito_redirect_uri:
            return self.cognito_redirect_uri
        if self.base_url:
            return f"{self.base_url.rstrip('/')}/"
        return None

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


@lru_cache
def get_settings() -> Settings:
    return Settings()

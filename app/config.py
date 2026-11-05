from functools import lru_cache

import boto3
from botocore.exceptions import BotoCoreError, ClientError
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    default_search_zip: str = "02169"
    cognito_region: str = "us-east-1"
    cognito_user_pool_id: str | None = None
    cognito_app_client_id: str | None = None

    # TeeItUp credentials stored in AWS Secrets Manager.
    teeitup_secret_arn: str | None = None

    @property
    def cognito_issuer(self) -> str | None:
        if not self.cognito_user_pool_id:
            return None
        return f"https://cognito-idp.{self.cognito_region}.amazonaws.com/{self.cognito_user_pool_id}"

    def get_teeitup_credentials(self) -> dict[str, str] | None:
        """Fetch TeeItUp credentials from AWS Secrets Manager.

        Returns None if not configured or the secret can't be read.
        The secret is expected to be a JSON object with at least:
        {\"username\": \"...\", \"password\": \"...\"}

        Never returns raw credentials to callers that don't need them.
        """
        if not self.teeitup_secret_arn:
            return None
        try:
            client = boto3.client("secretsmanager", region_name=self.cognito_region)
            response = client.get_secret_value(SecretId=self.teeitup_secret_arn)
            import json

            return json.loads(response["SecretString"])
        except (BotoCoreError, ClientError, ValueError, KeyError):
            return None

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


@lru_cache
def get_settings() -> Settings:
    return Settings()


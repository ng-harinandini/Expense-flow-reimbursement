from typing import Optional
from urllib.parse import quote_plus

from pydantic import Field, computed_field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables (and an optional .env file).

    Database URL resolution priority:
      1. DATABASE_URL (used verbatim if provided)
      2. Otherwise constructed from DB_USER / DB_PASSWORD / DB_HOST / DB_PORT / DB_NAME

    Secrets are never logged. AWS credentials are NOT read here — boto3 resolves them via its
    normal credential chain (env vars, shared config, instance/role profiles).
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # --- App metadata (preserved from previous config) ---
    PROJECT_NAME: str = "ExpenseFlow Enterprise API"
    VERSION: str = "1.0.0"
    API_PREFIX: str = "/api"
    GEMINI_API_KEY: str = ""

    # --- Database (PostgreSQL only) ---
    DATABASE_URL: Optional[str] = None
    DB_USER: Optional[str] = None
    DB_PASSWORD: Optional[str] = None
    DB_HOST: Optional[str] = None
    DB_PORT: int = 5432
    DB_NAME: Optional[str] = None

    # SQLAlchemy engine pool tuning
    DB_POOL_SIZE: int = 5
    DB_MAX_OVERFLOW: int = 10
    DB_POOL_RECYCLE_SECONDS: int = 1800

    # --- AWS / S3 / Textract ---
    AWS_REGION: str = "us-east-1"
    S3_BUCKET_NAME: Optional[str] = None
    TEXTRACT_ENABLED: bool = False

    # --- AWS Cognito (authentication) ---
    # Region is derived from the pool id prefix (e.g. "ap-south-1_xxxxx") when not set explicitly.
    COGNITO_REGION: Optional[str] = None
    COGNITO_USER_POOL_ID: Optional[str] = None
    COGNITO_APP_CLIENT_ID: Optional[str] = None
    COGNITO_APP_CLIENT_SECRET: Optional[str] = None  # only if the app client has a secret

    @computed_field  # type: ignore[prop-decorator]
    @property
    def database_url(self) -> Optional[str]:
        """Resolved SQLAlchemy database URL, or None if not configured.

        Uses DATABASE_URL verbatim when set; otherwise builds a psycopg (v3) URL from DB_* parts.
        Returns None when neither is fully configured so callers can degrade gracefully instead
        of crashing at import time.
        """
        if self.DATABASE_URL:
            return self.DATABASE_URL

        if self.DB_USER and self.DB_HOST and self.DB_NAME:
            password = f":{quote_plus(self.DB_PASSWORD)}" if self.DB_PASSWORD else ""
            user = quote_plus(self.DB_USER)
            return (
                f"postgresql+psycopg://{user}{password}"
                f"@{self.DB_HOST}:{self.DB_PORT}/{self.DB_NAME}"
            )

        return None

    @property
    def database_configured(self) -> bool:
        return self.database_url is not None

    @property
    def cognito_region(self) -> str:
        """Region for Cognito calls. Explicit COGNITO_REGION wins; otherwise derived from the
        user-pool id prefix (`ap-south-1_xxxxx` → `ap-south-1`); falls back to AWS_REGION."""
        if self.COGNITO_REGION:
            return self.COGNITO_REGION
        if self.COGNITO_USER_POOL_ID and "_" in self.COGNITO_USER_POOL_ID:
            return self.COGNITO_USER_POOL_ID.split("_", 1)[0]
        return self.AWS_REGION

    @property
    def cognito_configured(self) -> bool:
        return bool(self.COGNITO_USER_POOL_ID and self.COGNITO_APP_CLIENT_ID)


settings = Settings()

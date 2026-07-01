"""Application configuration loaded from environment variables."""

from pydantic_settings import BaseSettings
from typing import List
import json


class Settings(BaseSettings):
    """Central settings object — all config comes from env vars or .env file."""

    # Database
    DATABASE_URL: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/meetmind"

    # JWT
    JWT_SECRET: str = "change-me-to-a-real-secret-in-production"
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7

    # Azure AD / Microsoft Graph (Teams bot)
    AZURE_APP_ID: str = ""
    AZURE_APP_PASSWORD: str = ""
    AZURE_TENANT_ID: str = ""
    GRAPH_API_SCOPE: str = "https://graph.microsoft.com/.default"

    # Bot Framework
    BOT_FRAMEWORK_ENDPOINT: str = ""

    # LLM — Groq for summarization
    GROQ_API_KEY: str = ""
    GROQ_MODEL: str = "llama-3.3-70b-versatile"

    # Cognee Cloud — memory layer
    COGNEE_API_KEY: str = ""
    COGNEE_BASE_URL: str = "https://api.cognee.ai"

    # CORS
    BACKEND_CORS_ORIGINS: str = '["http://localhost:3000"]'

    @property
    def cors_origins(self) -> List[str]:
        return json.loads(self.BACKEND_CORS_ORIGINS)

    @property
    def azure_configured(self) -> bool:
        """Return True only if ALL required Azure vars are non-empty."""
        return bool(
            self.AZURE_APP_ID
            and self.AZURE_APP_PASSWORD
            and self.AZURE_TENANT_ID
        )

    @property
    def groq_configured(self) -> bool:
        """Return True if Groq API key is set for summarization."""
        return bool(self.GROQ_API_KEY)

    @property
    def cognee_configured(self) -> bool:
        """Return True if Cognee Cloud API key is set."""
        return bool(self.COGNEE_API_KEY)

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()

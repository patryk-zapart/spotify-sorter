"""Centralized, environment-based configuration.

All secrets/URLs are read from environment variables (via .env in local dev,
real environment variables in GCP). Nothing is hardcoded so the same image
can run in dev/staging/prod by changing env vars only.
"""
from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Spotify
    spotify_client_id: str = ""
    spotify_client_secret: str = ""
    spotify_redirect_uri: str = "http://127.0.0.1:8000/api/auth/callback"

    # OpenAI
    openai_api_key: str = ""
    openai_model: str = "gpt-4o-mini"

    # Audio feature strategy: "openai" (default, recommended) or "spotify" (grandfathered apps only)
    audio_features_provider: str = "openai"

    # App
    secret_key: str = "dev-secret-change-me"
    frontend_url: str = "http://127.0.0.1:5173"
    database_url: str = "sqlite:///./data/app.db"
    environment: str = "development"

    @property
    def is_production(self) -> bool:
        return self.environment.lower() == "production"


@lru_cache
def get_settings() -> Settings:
    return Settings()

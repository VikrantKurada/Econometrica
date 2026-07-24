"""Application settings, loaded from the environment and the repo-root ``.env``."""

from pathlib import Path

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# ``.env`` lives at the repository root but the package is run from ``backend/``.
# Resolve it from this file so settings load identically no matter the cwd.
_PACKAGE_DIR = Path(__file__).resolve().parent  # <repo>/backend/src/econometrica
_REPO_ROOT = _PACKAGE_DIR.parents[2]  # <repo>
_ENV_FILE = _REPO_ROOT / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=_ENV_FILE,
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    database_url: str
    test_database_url: str = ""

    # These three carry an ``ECONOMETRICA_`` prefix in ``.env`` to avoid colliding
    # with generic names already present in most shells.
    storage_dir: Path = Field(
        default=Path("./storage"),
        validation_alias=AliasChoices("ECONOMETRICA_STORAGE_DIR", "STORAGE_DIR"),
    )
    secret_key: str = Field(
        default="dev-only-insecure-key",
        validation_alias=AliasChoices("ECONOMETRICA_SECRET_KEY", "SECRET_KEY"),
    )
    log_level: str = Field(
        default="INFO",
        validation_alias=AliasChoices("ECONOMETRICA_LOG_LEVEL", "LOG_LEVEL"),
    )

    ollama_base_url: str = "http://localhost:11434"
    nvidia_api_key: str = ""
    gemini_api_key: str = ""
    anthropic_api_key: str = ""
    openai_api_key: str = ""

    otel_exporter_otlp_endpoint: str = ""


def get_settings() -> Settings:
    return Settings()

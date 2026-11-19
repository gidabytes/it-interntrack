"""Application configuration.

Everything is environment-overridable so the same build runs on a developer
laptop (SQLite) and on the internal IT network (PostgreSQL) without code
changes.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # --- Identity -----------------------------------------------------------
    APP_NAME: str = "IT InternTrack"
    APP_VERSION: str = "1.0.0"
    ORGANIZATION_NAME: str = "Information Technology Department"
    COMPANY_NAME: str = "Company Name"
    DEBUG: bool = False

    # --- Database -----------------------------------------------------------
    # SQLite for development / single-instance deployment.
    # For production set e.g.
    #   DATABASE_URL=postgresql+psycopg://user:pass@dbhost:5432/interntrack
    DATABASE_URL: str = f"sqlite:///{BASE_DIR / 'interntrack.db'}"
    SQL_ECHO: bool = False

    # --- Security -----------------------------------------------------------
    SECRET_KEY: str = "CHANGE-ME-IN-PRODUCTION-use-openssl-rand-hex-32"
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 480  # one 8h working session
    BCRYPT_ROUNDS: int = 12

    # --- Field-level encryption (Fernet) ------------------------------------
    # Comma-separated base64 Fernet keys. The FIRST key encrypts; all keys are
    # tried on decrypt, which is what allows rotation without downtime.
    # Generate with: python -m app.cli generate-key
    FERNET_KEYS: str = ""
    # Separate key for deterministic blind-index lookup hashes (hex).
    BLIND_INDEX_KEY: str = ""
    # Refuse to start without encryption keys unless DEBUG is on.
    REQUIRE_ENCRYPTION: bool = True

    # --- Business timezone --------------------------------------------------
    # All business dates, shift boundaries and reports use this zone.
    # Timestamps are persisted in UTC and rendered in this zone.
    BUSINESS_TIMEZONE: str = "Asia/Manila"

    # --- Uploads ------------------------------------------------------------
    UPLOAD_DIR: Path = BASE_DIR / "uploads"
    MAX_UPLOAD_BYTES: int = 5 * 1024 * 1024
    ALLOWED_UPLOAD_EXTENSIONS: tuple[str, ...] = (
        ".png", ".jpg", ".jpeg", ".pdf", ".docx",
    )

    # --- CORS ---------------------------------------------------------------
    CORS_ORIGINS: list[str] = ["http://localhost:5173", "http://localhost:3000"]

    @property
    def is_sqlite(self) -> bool:
        return self.DATABASE_URL.startswith("sqlite")


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()

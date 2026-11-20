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

    # Connection pool, for a direct PostgreSQL connection. Ignored behind a
    # transaction-mode pooler (Supabase port 6543), where the pooler does the
    # pooling and a second pool here would exhaust the connection limit.
    DB_POOL_SIZE: int = 5
    DB_MAX_OVERFLOW: int = 10
    # Recycle below typical idle timeouts so a stale connection is replaced
    # rather than failing a request.
    DB_POOL_RECYCLE: int = 1800

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

    @property
    def is_postgres(self) -> bool:
        return self.DATABASE_URL.startswith(("postgresql", "postgres://"))

    @property
    def is_supabase(self) -> bool:
        return "supabase.co" in self.DATABASE_URL.lower()

    @property
    def database_backend(self) -> str:
        """Human-readable backend name, for status output and logs."""
        if self.is_sqlite:
            return "sqlite"
        if self.is_supabase:
            return "postgresql (supabase)"
        if self.is_postgres:
            return "postgresql"
        return self.DATABASE_URL.split("://")[0]


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()

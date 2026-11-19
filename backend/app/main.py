"""IT InternTrack - application entrypoint."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.core.config import settings
from app.core.crypto import EncryptionNotConfigured, encryption_enabled, require_encryption
from app.core.database import SessionLocal, engine
from app.core.permissions import validate_catalogue
from app.core.timeutil import now_business

logging.basicConfig(
    level=logging.DEBUG if settings.DEBUG else logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s | %(message)s",
)
log = logging.getLogger("interntrack")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup checks.

    Deliberately fails fast on a misconfiguration that would silently store
    sensitive data in plaintext, rather than starting and hoping somebody
    notices later.
    """
    validate_catalogue()

    if settings.REQUIRE_ENCRYPTION:
        require_encryption(debug=settings.DEBUG)
    if not encryption_enabled():
        log.warning(
            "Field-level encryption is DISABLED. Intern PII will be stored in "
            "plaintext. Set FERNET_KEYS before handling real data."
        )
    if settings.SECRET_KEY.startswith("CHANGE-ME"):
        log.warning(
            "SECRET_KEY is still the shipped placeholder. Generate one with "
            "`openssl rand -hex 32` before deploying."
        )

    # Seed the settings catalogue and the standard shift matrix on first run.
    from app.services.settings_service import ensure_defaults
    from app.services.shift_service import ensure_standard_shifts

    with SessionLocal() as db:
        try:
            created_settings = ensure_defaults(db)
            created_shifts = ensure_standard_shifts(db)
            db.commit()
            if created_settings or created_shifts:
                log.info(
                    "Bootstrap: %d setting(s), %d shift template(s) created.",
                    created_settings, created_shifts,
                )
        except Exception:  # pragma: no cover - fresh DB without tables
            db.rollback()
            log.warning(
                "Bootstrap skipped: database tables are not present yet. "
                "Run `alembic upgrade head` (or `python -m app.cli init-db`)."
            )

    log.info(
        "%s %s ready | tz=%s | db=%s | encryption=%s",
        settings.APP_NAME, settings.APP_VERSION, settings.BUSINESS_TIMEZONE,
        "sqlite" if settings.is_sqlite else "postgresql",
        "on" if encryption_enabled() else "OFF",
    )
    yield
    engine.dispose()
    log.info("Shutdown complete.")


app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    description=(
        "OJT & Intern Attendance Management System for a 24/7 IT operation.\n\n"
        "Attendance is bound to the scheduled shift's START date, so an "
        "overnight session is one record. OJT hours derive from an append-only "
        "ledger rather than a running total."
    ),
    lifespan=lifespan,
    docs_url="/api/docs",
    redoc_url="/api/redoc",
    openapi_url="/api/openapi.json",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(RequestValidationError)
async def validation_handler(request: Request, exc: RequestValidationError):
    """Readable validation errors instead of raw pydantic output."""
    problems = []
    for err in exc.errors():
        loc = " -> ".join(str(p) for p in err["loc"] if p != "body")
        problems.append({"field": loc or "body", "message": err["msg"]})
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        content={"detail": "The request could not be processed.", "problems": problems},
    )


@app.exception_handler(EncryptionNotConfigured)
async def encryption_handler(request: Request, exc: EncryptionNotConfigured):
    log.error("Encryption failure: %s", exc)
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={
            "detail": (
                "The server cannot read or write encrypted fields. An "
                "administrator must check the FERNET_KEYS configuration."
            )
        },
    )


@app.get("/api/health", tags=["System"], summary="Health check")
def health() -> dict:
    """Liveness probe for nginx / monitoring. No authentication required."""
    return {
        "status": "ok",
        "application": settings.APP_NAME,
        "version": settings.APP_VERSION,
        "server_time": now_business().isoformat(),
        "business_timezone": settings.BUSINESS_TIMEZONE,
        "encryption": "enabled" if encryption_enabled() else "disabled",
    }


# --- Routers ---------------------------------------------------------------
from app.api.routers import (  # noqa: E402
    admin, attendance, auth, dashboard, exposure, interns, leave, ojt, reports,
    schedules, shifts, timesheets,
)

for module in (
    auth, interns, shifts, schedules, attendance, ojt, leave, timesheets,
    exposure, dashboard, reports, admin,
):
    app.include_router(module.router)

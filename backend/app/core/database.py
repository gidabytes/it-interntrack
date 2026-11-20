"""SQLAlchemy engine, session factory and declarative base.

Dialect-neutral: the same models run on SQLite (development / single-instance)
and PostgreSQL (production) by changing DATABASE_URL only.
"""
from __future__ import annotations

from collections.abc import Generator

from datetime import timezone

from sqlalchemy import DateTime, TypeDecorator, create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.pool import NullPool

from app.core.config import settings

def _is_transaction_pooler(url: str) -> bool:
    """Is this URL pointing at a transaction-mode connection pooler?

    Supabase exposes two endpoints:

      * port 5432 - a direct connection, or the session-mode pooler
      * port 6543 - the TRANSACTION-mode pooler (pgbouncer)

    Transaction mode hands a different backend connection to each statement,
    so server-side prepared statements break: psycopg prepares a statement on
    one backend and then tries to execute it on another, which fails with
    "prepared statement does not exist". Detected here so the right connection
    arguments are applied automatically rather than left as a trap.
    """
    lowered = url.lower()
    return ":6543" in lowered or "pooler.supabase.co" in lowered


def _build_engine_kwargs() -> tuple[dict, dict]:
    """Connection and engine arguments for the configured database."""
    connect_args: dict = {}
    engine_kwargs: dict = {
        "echo": settings.SQL_ECHO,
        "pool_pre_ping": True,
        "future": True,
    }

    if settings.is_sqlite:
        # FastAPI serves requests on a threadpool; SQLite needs this relaxed.
        connect_args["check_same_thread"] = False
        return connect_args, engine_kwargs

    # --- PostgreSQL ---------------------------------------------------------
    if _is_transaction_pooler(settings.DATABASE_URL):
        # Disable prepared statements entirely: required behind pgbouncer in
        # transaction mode. psycopg treats None as "never prepare".
        connect_args["prepare_threshold"] = None
        # The pooler is already doing the pooling. Keeping a second pool here
        # holds server-side connections open and will exhaust the project's
        # connection limit under load.
        engine_kwargs["poolclass"] = NullPool
    else:
        engine_kwargs["pool_size"] = settings.DB_POOL_SIZE
        engine_kwargs["max_overflow"] = settings.DB_MAX_OVERFLOW
        engine_kwargs["pool_recycle"] = settings.DB_POOL_RECYCLE

    return connect_args, engine_kwargs


connect_args, engine_kwargs = _build_engine_kwargs()

engine = create_engine(settings.DATABASE_URL, connect_args=connect_args, **engine_kwargs)


if settings.is_sqlite:

    @event.listens_for(engine, "connect")
    def _sqlite_pragmas(dbapi_conn, _record):  # pragma: no cover - driver hook
        """SQLite does not enforce foreign keys unless asked.

        Without this the FK constraints in the models are decorative, and the
        ledger could orphan. WAL keeps concurrent reads working while the
        attendance engine writes inside a transaction.
        """
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA synchronous=NORMAL")
        cur.close()


SessionLocal = sessionmaker(
    bind=engine, autoflush=False, autocommit=False, expire_on_commit=False
)


class Base(DeclarativeBase):
    """Declarative base for every model."""


def get_db() -> Generator[Session, None, None]:
    """FastAPI dependency yielding a request-scoped session."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


class UTCDateTime(TypeDecorator):
    """A timezone-aware datetime column that is ALWAYS aware UTC in Python.

    SQLite has no native timestamp type and hands back naive datetimes even
    from a ``DateTime(timezone=True)`` column. Mixing those with the aware
    datetimes the application creates raises
    "can't compare offset-naive and offset-aware datetimes" at the worst
    possible moment - inside an attendance computation.

    This decorator removes the whole class of bug:

    * on write, a naive value is interpreted as BUSINESS-LOCAL time (that is
      what an API client without an offset means) and converted to UTC;
    * on read, a naive value is labelled UTC, and an aware one normalised to
      UTC.

    PostgreSQL already round-trips offsets correctly; the decorator is a no-op
    there beyond the normalisation, so the two dialects behave identically.
    """

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value, dialect):  # noqa: D102
        if value is None:
            return None
        if value.tzinfo is None:
            from app.core.timeutil import business_tz

            value = value.replace(tzinfo=business_tz())
        return value.astimezone(timezone.utc)

    def process_result_value(self, value, dialect):  # noqa: D102
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

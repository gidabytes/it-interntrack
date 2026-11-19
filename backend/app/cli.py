"""Command-line administration.

    python -m app.cli generate-key              # new Fernet key
    python -m app.cli generate-blind-index-key
    python -m app.cli init-db                   # create tables (dev; use alembic in prod)
    python -m app.cli bootstrap --password ...  # settings, shifts, admin user
    python -m app.cli seed-demo                 # realistic DEMO DATA
    python -m app.cli purge-demo
    python -m app.cli rotate-encryption         # re-encrypt under the primary key
    python -m app.cli finalize-day 2026-11-20
    python -m app.cli status
"""
from __future__ import annotations

import argparse
import sys
from datetime import date

from sqlalchemy import select

from app.core.config import settings
from app.core.crypto import (
    encryption_enabled, generate_blind_index_key, generate_key, reset_cipher_cache,
)
from app.core.database import Base, SessionLocal, engine


def cmd_generate_key(_args) -> int:
    key = generate_key()
    print(key)
    print(
        "\nAdd to .env as the FIRST entry to make it the primary encryption key:\n"
        f"  FERNET_KEYS={key}\n"
        "To rotate, prepend the new key and keep the old one until you have run\n"
        "  python -m app.cli rotate-encryption",
        file=sys.stderr,
    )
    return 0


def cmd_generate_blind_index_key(_args) -> int:
    print(generate_blind_index_key())
    print(
        "\nAdd to .env as BLIND_INDEX_KEY. Changing it invalidates every stored\n"
        "lookup index, so run rotate-encryption afterwards to rebuild them.",
        file=sys.stderr,
    )
    return 0


def cmd_init_db(_args) -> int:
    """Create tables directly. For development; production uses Alembic."""
    import app.models  # noqa: F401  - registers the metadata

    Base.metadata.create_all(engine)
    print(f"Created {len(Base.metadata.tables)} table(s) in {settings.DATABASE_URL}")
    return 0


def cmd_bootstrap(args) -> int:
    from app.seed import create_admin, seed_rbac
    from app.services.settings_service import ensure_defaults
    from app.services.shift_service import ensure_standard_shifts

    with SessionLocal() as db:
        n_settings = ensure_defaults(db)
        n_shifts = ensure_standard_shifts(db)
        seed_rbac(db)
        admin = create_admin(
            db, username=args.username, password=args.password,
            email=args.email or "admin@interntrack.local",
        )
        db.commit()
        print(f"Settings created     : {n_settings}")
        print(f"Shift templates      : {n_shifts} (17 standard expected on first run)")
        print(f"Administrator        : {admin.username}")
        if args.password:
            print("Password             : (as supplied)")
        else:
            from app.seed import DEMO_PASSWORD
            print(f"Password             : {DEMO_PASSWORD}  <-- CHANGE ON FIRST SIGN-IN")
    return 0


def cmd_seed_demo(_args) -> int:
    from app.seed import seed_demo

    with SessionLocal() as db:
        result = seed_demo(db)
        db.commit()
    print("DEMO DATA created:")
    for k, v in result.items():
        print(f"  {k:18s} {v}")
    print(
        "\nEvery row is marked 'DEMO DATA'. Remove with: "
        "python -m app.cli purge-demo"
    )
    return 0


def cmd_purge_demo(_args) -> int:
    from app.seed import purge_demo

    with SessionLocal() as db:
        counts = purge_demo(db)
        db.commit()
    print("Removed:", counts)
    return 0


def cmd_rotate_encryption(_args) -> int:
    """Re-encrypt every encrypted column under the current primary key.

    Reads with the full key list and writes back, which re-encrypts under the
    first key. Run it after prepending a new key, then drop the old key.
    """
    import app.models as m
    from app.core.crypto import EncryptedString, EncryptedText

    if not encryption_enabled():
        print("FERNET_KEYS is not set; nothing to rotate.", file=sys.stderr)
        return 1

    targets: list[tuple[type, list[str]]] = []
    for mapper in Base.registry.mappers:
        cls = mapper.class_
        cols = [
            c.key for c in mapper.column_attrs
            if isinstance(getattr(cls, c.key).type, (EncryptedString, EncryptedText))
            if hasattr(getattr(cls, c.key), "type")
        ]
        if cols:
            targets.append((cls, cols))

    total = 0
    with SessionLocal() as db:
        for cls, cols in targets:
            rows = db.scalars(select(cls)).all()
            for row in rows:
                for col in cols:
                    # Reading decrypts; assigning the same value re-encrypts
                    # under the primary key on flush.
                    value = getattr(row, col)
                    if value:
                        setattr(row, col, value)
                if hasattr(row, "sync_lookup_indexes"):
                    row.sync_lookup_indexes()
                total += 1
            db.flush()
            print(f"  {cls.__name__:22s} {len(rows):5d} row(s), columns: {', '.join(cols)}")
        db.commit()
    print(f"\nRe-encrypted {total} row(s) under the primary key.")
    print("You can now remove the old key from FERNET_KEYS.")
    return 0


def cmd_finalize_day(args) -> int:
    from app.services.attendance_service import finalize_absences
    from app.services.settings_service import load_rules

    target = date.fromisoformat(args.business_date)
    with SessionLocal() as db:
        rules = load_rules(db)
        counts = finalize_absences(db, target, rules=rules)
        db.commit()
    print(f"Finalised {target}: {counts}")
    return 0


def cmd_status(_args) -> int:
    import app.models as m
    from app.models.identity import User
    from app.models.intern import Intern
    from app.models.schedule import ShiftTemplate
    from sqlalchemy import func

    print(f"Application   : {settings.APP_NAME} {settings.APP_VERSION}")
    print(f"Database      : {settings.DATABASE_URL.split('://')[0]}")
    print(f"Timezone      : {settings.BUSINESS_TIMEZONE}")
    print(f"Encryption    : {'ENABLED' if encryption_enabled() else 'DISABLED'}")
    print(f"Secret key    : {'DEFAULT - CHANGE IT' if settings.SECRET_KEY.startswith('CHANGE-ME') else 'set'}")
    try:
        with SessionLocal() as db:
            print(f"Users         : {db.scalar(select(func.count(User.id))) or 0}")
            print(f"Interns       : {db.scalar(select(func.count(Intern.id))) or 0}")
            print(f"Shift templates: {db.scalar(select(func.count(ShiftTemplate.id))) or 0}")
    except Exception as exc:
        print(f"Database      : NOT READY ({type(exc).__name__})")
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="app.cli", description="IT InternTrack administration"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("generate-key", help="Generate a Fernet encryption key")
    sub.add_parser("generate-blind-index-key", help="Generate a blind-index key")
    sub.add_parser("init-db", help="Create tables directly (development)")

    p = sub.add_parser("bootstrap", help="Seed settings, shifts and an admin user")
    p.add_argument("--username", default="admin")
    p.add_argument("--password", default=None)
    p.add_argument("--email", default=None)

    sub.add_parser("seed-demo", help="Create realistic DEMO DATA")
    sub.add_parser("purge-demo", help="Remove DEMO DATA")
    sub.add_parser("rotate-encryption", help="Re-encrypt under the primary key")

    p = sub.add_parser("finalize-day", help="Mark absences for a business date")
    p.add_argument("business_date", help="YYYY-MM-DD")

    sub.add_parser("status", help="Show configuration and row counts")

    args = parser.parse_args(argv)
    handlers = {
        "generate-key": cmd_generate_key,
        "generate-blind-index-key": cmd_generate_blind_index_key,
        "init-db": cmd_init_db,
        "bootstrap": cmd_bootstrap,
        "seed-demo": cmd_seed_demo,
        "purge-demo": cmd_purge_demo,
        "rotate-encryption": cmd_rotate_encryption,
        "finalize-day": cmd_finalize_day,
        "status": cmd_status,
    }
    return handlers[args.command](args)


if __name__ == "__main__":
    raise SystemExit(main())

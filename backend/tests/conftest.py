"""Shared test fixtures.

Encryption keys are generated per session BEFORE the application modules are
imported, so every test exercises the same encrypted-at-rest path that
production uses rather than a plaintext shortcut.
"""
from __future__ import annotations

import os
from datetime import date, time, timedelta

import pytest

# Must be set before app.core.config is imported.
os.environ.setdefault("DATABASE_URL", "sqlite://")
os.environ.setdefault("SECRET_KEY", "test-secret-key-not-for-production-use-only")
os.environ.setdefault("DEBUG", "true")
os.environ.setdefault("REQUIRE_ENCRYPTION", "false")

from cryptography.fernet import Fernet  # noqa: E402

os.environ.setdefault("FERNET_KEYS", Fernet.generate_key().decode())
os.environ.setdefault("BLIND_INDEX_KEY", os.urandom(32).hex())

from sqlalchemy import create_engine, event, select  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

from app.core.crypto import reset_cipher_cache  # noqa: E402
from app.core.database import Base  # noqa: E402
from app.core.enums import ApprovalStatus, InternStatus, RoleName  # noqa: E402
from app.core.timeutil import combine_business  # noqa: E402
import app.models as m  # noqa: E402
from app.services.settings_service import ensure_defaults, load_rules  # noqa: E402
from app.services.shift_service import ensure_standard_shifts  # noqa: E402

reset_cipher_cache()

# A fixed "today" so date-sensitive assertions never drift.
TODAY = date(2026, 11, 20)          # a Friday
MONDAY = date(2026, 11, 16)


# Set TEST_DATABASE_URL to run the whole suite against PostgreSQL instead of
# in-memory SQLite. Both must pass: the models are dialect-neutral and this is
# how that stays true.
TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")


@pytest.fixture(scope="function")
def db():
    """One isolated database per test, with foreign keys enforced.

    SQLite: in-memory. PostgreSQL: a throwaway schema per test, dropped after,
    which gives the same isolation without a server round-trip per table.
    """
    if TEST_DATABASE_URL:
        import uuid

        schema = f"t{uuid.uuid4().hex[:12]}"
        admin = create_engine(TEST_DATABASE_URL, isolation_level="AUTOCOMMIT")
        with admin.connect() as c:
            c.exec_driver_sql(f'CREATE SCHEMA "{schema}"')
        admin.dispose()

        engine = create_engine(
            TEST_DATABASE_URL,
            connect_args={"options": f"-csearch_path={schema}"},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(engine)
        Session = sessionmaker(bind=engine, expire_on_commit=False)
        session = Session()
        ensure_defaults(session)
        ensure_standard_shifts(session)
        session.commit()
        try:
            yield session
        finally:
            session.close()
            engine.dispose()
            admin = create_engine(TEST_DATABASE_URL, isolation_level="AUTOCOMMIT")
            with admin.connect() as c:
                c.exec_driver_sql(f'DROP SCHEMA "{schema}" CASCADE')
            admin.dispose()
        return

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def _fk(dbapi_conn, _rec):
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    session = Session()
    ensure_defaults(session)
    ensure_standard_shifts(session)
    session.commit()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


@pytest.fixture
def rules(db):
    return load_rules(db)


@pytest.fixture
def shifts(db):
    """All shift templates, keyed by code."""
    return {t.code: t for t in db.scalars(select(m.ShiftTemplate)).all()}


@pytest.fixture
def org(db):
    """Department, team, roles, supervisor and mentor."""
    from app.core.permissions import ROLE_DISPLAY, ROLE_PERMISSIONS
    from app.core.security import hash_password

    perms = {}
    from app.core.permissions import PERMISSIONS

    for code, desc in PERMISSIONS.items():
        p = m.Permission(code=code, description=desc)
        db.add(p)
        perms[code] = p
    db.flush()

    roles = {}
    for name, codes in ROLE_PERMISSIONS.items():
        r = m.Role(name=name, display_name=ROLE_DISPLAY.get(name, name))
        r.permissions = [perms[c] for c in sorted(codes)]
        db.add(r)
        roles[name] = r
    db.flush()

    dept = m.Department(code="ITD", name="IT Department", site_code="DGT")
    db.add(dept)
    db.flush()
    team = m.Team(code="NOC", name="Network Operations", department_id=dept.id)
    db.add(team)
    db.flush()

    sup_user = m.User(
        username="supervisor", email="sup@test.local",
        password_hash=hash_password("TestPass12345"),
        full_name="Reymond Santos", position_title="IT Supervisor",
    )
    sup_user.sync_lookup_indexes()
    sup_user.roles = [roles[RoleName.IT_SUPERVISOR]]
    db.add(sup_user)
    db.flush()
    supervisor = m.Supervisor(user_id=sup_user.id, department_id=dept.id)
    db.add(supervisor)

    mentor_user = m.User(
        username="mentor", email="mentor@test.local",
        password_hash=hash_password("TestPass12345"), full_name="Jenny Valdez",
    )
    mentor_user.sync_lookup_indexes()
    mentor_user.roles = [roles[RoleName.IT_MENTOR]]
    db.add(mentor_user)
    db.flush()
    mentor = m.Mentor(user_id=mentor_user.id, team_id=team.id)
    db.add(mentor)

    admin_user = m.User(
        username="admin", email="admin@test.local",
        password_hash=hash_password("TestPass12345"), full_name="System Administrator",
    )
    admin_user.sync_lookup_indexes()
    admin_user.roles = [roles[RoleName.SYSTEM_ADMINISTRATOR]]
    db.add(admin_user)
    db.flush()

    return {
        "roles": roles, "department": dept, "team": team,
        "supervisor": supervisor, "supervisor_user": sup_user,
        "mentor": mentor, "mentor_user": mentor_user,
        "admin_user": admin_user,
    }


@pytest.fixture
def make_intern(db, org):
    """Factory: create an intern, optionally with a weekly schedule."""
    counter = {"n": 0}

    def _make(
        *,
        first_name: str = "Ana",
        last_name: str = "Cruz",
        shift=None,
        work_days: tuple[int, ...] = (0, 1, 2, 3, 4),
        rest_days: tuple[int, ...] | None = None,
        start: date = date(2026, 11, 2),
        required_hours: int = 486,
        school_deadline: date | None = None,
        with_user: bool = False,
        status: str = InternStatus.ACTIVE,
    ):
        counter["n"] += 1
        intern = m.Intern(
            intern_code=f"OJT-2026-{counter['n']:04d}",
            first_name=first_name,
            last_name=last_name,
            email=f"{first_name.lower()}.{last_name.lower()}{counter['n']}@test.local",
            contact_number=f"0917-555-{counter['n']:04d}",
            ojt_start_date=start,
            required_hours=required_hours,
            school_submission_deadline=school_deadline,
            department_id=org["department"].id,
            team_id=org["team"].id,
            supervisor_id=org["supervisor"].id,
            mentor_id=org["mentor"].id,
            status=status,
        )
        intern.sync_lookup_indexes()
        db.add(intern)
        db.flush()

        if with_user:
            from app.core.security import hash_password

            u = m.User(
                username=f"intern{counter['n']}",
                email=f"intern{counter['n']}@test.local",
                password_hash=hash_password("TestPass12345"),
                full_name=f"{first_name} {last_name}",
            )
            u.sync_lookup_indexes()
            u.roles = [org["roles"][RoleName.INTERN]]
            db.add(u)
            db.flush()
            intern.user_id = u.id

        if shift is not None:
            for dow in work_days:
                db.add(m.ScheduleAssignment(
                    intern_id=intern.id,
                    shift_template_id=shift.id,
                    assignment_type="WEEKLY_RECURRING",
                    day_of_week=dow,
                    effective_from=start,
                    approval_status=ApprovalStatus.APPROVED,
                ))
            for dow in (
                rest_days if rest_days is not None
                else tuple(set(range(7)) - set(work_days))
            ):
                db.add(m.RestDay(
                    intern_id=intern.id, day_of_week=dow, effective_from=start
                ))
        db.flush()
        return intern

    return _make


@pytest.fixture
def punch():
    """Build a business-local timestamp: punch(date, 22, 5) -> 22:05 that day."""
    def _punch(d: date, hour: int, minute: int = 0):
        return combine_business(d, time(hour, minute))

    return _punch


@pytest.fixture
def approve(db, org):
    """Approve a record and sync the ledger, as a supervisor would."""
    from app.services import ojt_engine

    def _approve(record, rules):
        from app.core.timeutil import utc_now

        record.approval_status = ApprovalStatus.APPROVED
        record.approved_by_id = org["supervisor_user"].id
        record.approved_at = utc_now()
        ojt_engine.post_attendance(
            db, record, actor_user_id=org["supervisor_user"].id, rules=rules
        )
        ojt_engine.approve_ledger_for_record(
            db, record, actor_user_id=org["supervisor_user"].id
        )
        db.flush()
        return record

    return _approve

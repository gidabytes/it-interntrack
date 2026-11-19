"""End-to-end API tests through the real FastAPI stack.

These exercise authentication, role-scoped access and the full intern lifecycle
over HTTP, so a wiring mistake between router, service and database is caught
rather than assumed away.
"""
from __future__ import annotations

from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base, get_db
from app.core.enums import RoleName
from app.main import app
import app.models as m

MON = date(2026, 11, 16)
FRI = date(2026, 11, 20)


@pytest.fixture
def api():
    """A TestClient wired to a dedicated in-memory database."""
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )

    @event.listens_for(engine, "connect")
    def _fk(c, _r):
        cur = c.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)

    # Bootstrap: settings, shifts, roles, users.
    from app.core.security import hash_password
    from app.seed import seed_rbac
    from app.services.settings_service import ensure_defaults
    from app.services.shift_service import ensure_standard_shifts

    with Session() as s:
        ensure_defaults(s)
        ensure_standard_shifts(s)
        roles = seed_rbac(s)

        dept = m.Department(code="ITD", name="IT Department", site_code="DGT")
        s.add(dept)
        s.flush()
        team = m.Team(code="NOC", name="Network Operations", department_id=dept.id)
        s.add(team)
        s.flush()

        def mkuser(username, name, role, position=None):
            u = m.User(
                username=username, email=f"{username}@test.local",
                password_hash=hash_password("TestPass12345"), full_name=name,
                position_title=position, department_id=dept.id,
            )
            u.sync_lookup_indexes()
            u.roles = [roles[role]]
            s.add(u)
            s.flush()
            return u

        admin = mkuser("admin", "Administrator", RoleName.SYSTEM_ADMINISTRATOR)
        sup_u = mkuser("sup", "Reymond Santos", RoleName.IT_SUPERVISOR, "IT Supervisor")
        mkuser("mgr", "IT Manager", RoleName.IT_MANAGER)
        sup = m.Supervisor(user_id=sup_u.id, department_id=dept.id, team_id=team.id)
        s.add(sup)
        s.flush()

        intern_u = mkuser("ana", "Ana Cruz", RoleName.INTERN)
        s.commit()
        ids = {
            "dept": dept.id, "team": team.id, "supervisor": sup.id,
            "intern_user": intern_u.id,
        }

    def override():
        db = Session()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override
    client = TestClient(app)
    client.ids = ids
    client.SessionLocal = Session
    try:
        yield client
    finally:
        app.dependency_overrides.clear()
        engine.dispose()


def login(api, username: str, password: str = "TestPass12345") -> dict:
    r = api.post("/api/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


# ---------------------------------------------------------------------------
# Health and auth
# ---------------------------------------------------------------------------

def test_health_needs_no_authentication(api):
    r = api.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["business_timezone"] == "Asia/Manila"


def test_login_and_me(api):
    headers = login(api, "sup")
    r = api.get("/api/auth/me", headers=headers)
    assert r.status_code == 200
    body = r.json()
    assert body["username"] == "sup"
    assert body["email"] == "sup@test.local", "decrypted for the owner"
    assert "attendance.approve" in body["permissions"]
    assert "user.manage" not in body["permissions"]


def test_login_with_wrong_password_is_generic(api):
    r = api.post("/api/auth/login", json={"username": "sup", "password": "wrong"})
    assert r.status_code == 401
    assert r.json()["detail"] == "Incorrect username or password."


def test_login_with_unknown_user_gives_the_same_message(api):
    """No user enumeration: the message must not differ."""
    r = api.post("/api/auth/login", json={"username": "nobody", "password": "wrong"})
    assert r.status_code == 401
    assert r.json()["detail"] == "Incorrect username or password."


def test_login_by_email_works_through_the_blind_index(api):
    r = api.post(
        "/api/auth/login",
        json={"username": "sup@test.local", "password": "TestPass12345"},
    )
    assert r.status_code == 200


def test_repeated_failures_lock_the_account(api):
    for _ in range(5):
        api.post("/api/auth/login", json={"username": "ana", "password": "bad"})
    r = api.post("/api/auth/login", json={"username": "ana", "password": "TestPass12345"})
    assert r.status_code == 423, "account locked after repeated failures"
    assert "locked" in r.json()["detail"].lower()


def test_protected_endpoint_requires_a_token(api):
    assert api.get("/api/interns").status_code == 401
    assert api.get("/api/dashboard").status_code == 401


def test_invalid_token_is_rejected(api):
    r = api.get("/api/interns", headers={"Authorization": "Bearer not.a.token"})
    assert r.status_code == 401


# ---------------------------------------------------------------------------
# RBAC over HTTP
# ---------------------------------------------------------------------------

def test_intern_cannot_create_interns(api):
    headers = login(api, "ana")
    r = api.post("/api/interns", headers=headers, json={
        "first_name": "Mal", "last_name": "Actor",
        "ojt_start_date": str(MON), "required_hours": 486,
    })
    assert r.status_code == 403
    assert "does not grant permission" in r.json()["detail"]


def test_intern_cannot_reach_admin_endpoints(api):
    headers = login(api, "ana")
    assert api.get("/api/admin/users", headers=headers).status_code == 403
    assert api.get("/api/admin/settings", headers=headers).status_code == 403
    assert api.get("/api/admin/audit", headers=headers).status_code == 403


def test_supervisor_cannot_manage_users(api):
    headers = login(api, "sup")
    assert api.get("/api/admin/users", headers=headers).status_code == 403


def test_administrator_can_manage_users(api):
    headers = login(api, "admin")
    r = api.get("/api/admin/users", headers=headers)
    assert r.status_code == 200
    assert len(r.json()) >= 4


# ---------------------------------------------------------------------------
# Shift selector
# ---------------------------------------------------------------------------

def test_shift_selector_returns_all_seventeen(api):
    headers = login(api, "sup")
    r = api.get("/api/shifts/selector", headers=headers)
    assert r.status_code == 200
    body = r.json()
    assert body["count"] == 17
    assert len(body["day_shifts"]) == 8, "SHIFT-01..08 do not cross midnight"
    assert len(body["overnight_shifts"]) == 9, "SHIFT-09..17 do"
    for o in body["options"]:
        assert o["elapsed_minutes"] == 540
        assert o["net_working_minutes"] == 480
        assert o["break_minutes"] == 60


# ---------------------------------------------------------------------------
# Full lifecycle
# ---------------------------------------------------------------------------

def test_full_intern_lifecycle_over_http(api):
    """Register, schedule, punch an overnight shift, approve, verify progress."""
    sup = login(api, "sup")

    # 1. Register
    r = api.post("/api/interns", headers=sup, json={
        "first_name": "Carla", "last_name": "Dizon",
        "email": "carla.dizon@school.example",
        "contact_number": "0917-555-0101",
        "ojt_start_date": str(MON),
        "school_submission_deadline": str(MON + timedelta(days=120)),
        "required_hours": 486,
        "team_id": api.ids["team"],
        "department_id": api.ids["dept"],
        "supervisor_id": api.ids["supervisor"],
        "status": "ACTIVE",
    })
    assert r.status_code == 201, r.text
    intern = r.json()
    assert intern["intern_code"].startswith("OJT-2026-")
    assert intern["email"] == "carla.dizon@school.example", "decrypted on read"
    intern_id = intern["id"]

    # 2. Assign the overnight shift, weekdays
    shifts = api.get("/api/shifts", headers=sup).json()
    s16 = next(s for s in shifts if s["code"] == "SHIFT-16")
    assert s16["is_overnight"] is True
    assert s16["net_working_minutes"] == 480

    for dow in range(5):
        r = api.post("/api/schedules/assignments", headers=sup, json={
            "intern_id": intern_id, "shift_template_id": s16["id"],
            "assignment_type": "WEEKLY_RECURRING", "day_of_week": dow,
            "effective_from": str(MON),
        })
        assert r.status_code == 201, r.text

    # 3. Calendar resolves correctly
    r = api.get(
        f"/api/schedules/calendar/{intern_id}", headers=sup,
        params={"date_from": str(MON), "date_to": str(MON + timedelta(days=6))},
    )
    assert r.status_code == 200
    cal = r.json()
    assert cal["working_days"] == 5
    assert cal["scheduled_hours"] == 40.0

    # 4. Supervisor records an overnight session crossing midnight
    r = api.post("/api/attendance/manual", headers=sup, json={
        "intern_id": intern_id,
        "business_date": str(FRI),
        "time_in": f"{FRI}T21:55:00+08:00",
        "time_out": f"{FRI + timedelta(days=1)}T07:05:00+08:00",
    })
    assert r.status_code == 200, r.text
    rec = r.json()
    assert rec["business_date"] == str(FRI), "dated to the shift start"
    assert rec["is_overnight_shift"] is True
    assert rec["actual_net_minutes"] == 490
    assert rec["creditable_minutes"] == 480
    assert rec["approval_status"] == "PENDING"
    record_id = rec["id"]

    # 5. Hours are pending, not approved
    r = api.get(f"/api/ojt/progress/{intern_id}", headers=sup)
    assert r.status_code == 200
    prog = r.json()["progress"]
    assert prog["approved_hours"] == 0.0
    assert prog["pending_hours"] == 8.0

    # 6. Approve
    r = api.post(f"/api/attendance/{record_id}/approve", headers=sup,
                 json={"remarks": "Verified against the duty roster."})
    assert r.status_code == 200, r.text
    assert r.json()["approval_status"] == "APPROVED"

    r = api.get(f"/api/ojt/progress/{intern_id}", headers=sup)
    body = r.json()
    assert body["progress"]["approved_hours"] == 8.0
    assert body["progress"]["remaining_hours"] == 478.0
    assert body["progress"]["completion_percentage"] == 1.65
    assert body["forecast"]["risk"] in (
        "ON_TRACK", "AT_RISK", "WILL_MISS_DEADLINE", "NO_DATA"
    )

    # 7. Ledger shows exactly one live posting
    r = api.get(f"/api/ojt/ledger/{intern_id}", headers=sup)
    entries = r.json()["entries"]
    live = [e for e in entries if e["counts_toward_total"]]
    assert len(live) == 1
    assert live[0]["minutes_delta"] == 480

    # 8. Duty board and dashboard respond with real data
    r = api.get("/api/dashboard/duty-board", headers=sup)
    assert r.status_code == 200
    assert "buckets" in r.json()

    r = api.get("/api/dashboard", headers=sup, params={"preset": "THIS_MONTH"})
    assert r.status_code == 200
    kpi = r.json()["kpi"]
    assert kpi["total_active_interns"] >= 1
    assert kpi["total_hours_rendered"] >= 8.0

    # 9. Timesheet: generate, submit, sign, verify
    r = api.post("/api/timesheets/generate", headers=sup, json={
        "intern_id": intern_id, "period_type": "MONTHLY", "anchor_date": str(FRI),
    })
    assert r.status_code == 200, r.text
    ts = r.json()
    assert ts["total_creditable_minutes"] == 480
    assert ts["status"] == "DRAFT"
    ts_id = ts["id"]

    r = api.post(f"/api/timesheets/{ts_id}/submit", headers=sup)
    assert r.status_code == 200

    r = api.post(f"/api/timesheets/{ts_id}/sign", headers=sup,
                 json={"remarks": "Hours verified."})
    assert r.status_code == 200, r.text
    signed = r.json()
    assert signed["status"] == "SIGNED"
    assert signed["signed_by_name"] == "Reymond Santos"
    assert signed["signature_hash"]

    r = api.get(f"/api/timesheets/{ts_id}/verify", headers=sup)
    assert r.json()["valid"] is True

    # 10. Reports export in every format
    for fmt, prefix in [
        ("xlsx", b"PK"), ("pdf", b"%PDF"), ("csv", None),
    ]:
        r = api.get("/api/reports/daily_attendance", headers=sup, params={
            "fmt": fmt, "preset": "CUSTOM",
            "date_from": str(MON), "date_to": str(FRI + timedelta(days=1)),
        })
        assert r.status_code == 200, f"{fmt}: {r.text[:200]}"
        assert len(r.content) > 100
        if prefix:
            assert r.content.startswith(prefix), f"{fmt} magic bytes"

    r = api.get("/api/reports/daily_attendance", headers=sup, params={
        "fmt": "json", "preset": "CUSTOM",
        "date_from": str(MON), "date_to": str(FRI + timedelta(days=1)),
    })
    assert r.status_code == 200
    assert r.json()["row_count"] >= 1


def test_intern_self_service_punch_cycle(api):
    """An intern times in and out for themselves; timestamps are server-set."""
    sup = login(api, "sup")

    with api.SessionLocal() as s:
        from app.core.enums import ApprovalStatus
        intern = m.Intern(
            intern_code="OJT-2026-5555", first_name="Ana", last_name="Cruz",
            ojt_start_date=MON, required_hours=486, status="ACTIVE",
            department_id=api.ids["dept"], team_id=api.ids["team"],
            supervisor_id=api.ids["supervisor"], user_id=api.ids["intern_user"],
        )
        intern.sync_lookup_indexes()
        s.add(intern)
        s.flush()
        shift = s.scalar(
            __import__("sqlalchemy").select(m.ShiftTemplate).where(
                m.ShiftTemplate.code == "SHIFT-02"
            )
        )
        for dow in range(7):
            s.add(m.ScheduleAssignment(
                intern_id=intern.id, shift_template_id=shift.id,
                assignment_type="WEEKLY_RECURRING", day_of_week=dow,
                effective_from=MON, approval_status=ApprovalStatus.APPROVED,
            ))
        s.commit()
        intern_id = intern.id

    ana = login(api, "ana")

    r = api.get("/api/attendance/my-status", headers=ana)
    assert r.status_code == 200
    assert r.json()["is_timed_in"] is False
    assert r.json()["can_time_in"] is True

    # An intern may not set their own timestamp.
    r = api.post("/api/attendance/time-in", headers=ana,
                 json={"timestamp": f"{MON}T08:00:00+08:00"})
    assert r.status_code == 403
    assert "cannot set the timestamp" in r.json()["detail"]

    r = api.post("/api/attendance/time-in", headers=ana, json={})
    assert r.status_code == 200, r.text
    assert r.json()["time_in"] is not None

    # Duplicate time-in refused.
    r = api.post("/api/attendance/time-in", headers=ana, json={})
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "DUPLICATE_TIME_IN"

    r = api.get("/api/attendance/my-status", headers=ana)
    assert r.json()["is_timed_in"] is True
    assert r.json()["can_time_out"] is True

    r = api.post("/api/attendance/time-out", headers=ana, json={})
    assert r.status_code == 200, r.text

    # The intern can see their own record and progress but not others'.
    r = api.get("/api/attendance", headers=ana)
    assert r.status_code == 200
    assert r.json()["total"] >= 1

    r = api.get("/api/ojt/progress/me", headers=ana)
    assert r.status_code == 200
    assert r.json()["intern"]["intern_code"] == "OJT-2026-5555"


def test_intern_cannot_approve_their_own_attendance(api):
    """The control that keeps the hour ledger trustworthy, over HTTP."""
    sup = login(api, "sup")
    with api.SessionLocal() as s:
        from app.core.enums import ApprovalStatus
        intern = m.Intern(
            intern_code="OJT-2026-6666", first_name="Ana", last_name="Cruz",
            ojt_start_date=MON, required_hours=486, status="ACTIVE",
            supervisor_id=api.ids["supervisor"], user_id=api.ids["intern_user"],
        )
        intern.sync_lookup_indexes()
        s.add(intern)
        s.flush()
        shift = s.scalar(
            __import__("sqlalchemy").select(m.ShiftTemplate).where(
                m.ShiftTemplate.code == "SHIFT-02"
            )
        )
        s.add(m.ScheduleAssignment(
            intern_id=intern.id, shift_template_id=shift.id,
            assignment_type="WEEKLY_RECURRING", day_of_week=MON.weekday(),
            effective_from=MON, approval_status=ApprovalStatus.APPROVED,
        ))
        s.commit()
        intern_id = intern.id

    r = api.post("/api/attendance/manual", headers=sup, json={
        "intern_id": intern_id, "business_date": str(MON),
        "time_in": f"{MON}T08:00:00+08:00", "time_out": f"{MON}T17:00:00+08:00",
    })
    assert r.status_code == 200, r.text
    record_id = r.json()["id"]

    ana = login(api, "ana")
    r = api.post(f"/api/attendance/{record_id}/approve", headers=ana, json={})
    assert r.status_code == 403
    assert "does not grant permission" in r.json()["detail"] or \
           "cannot approve" in str(r.json()["detail"])


def test_schedule_conflict_validation_over_http(api):
    """Assigning an overlapping shift is refused with a usable explanation."""
    sup = login(api, "sup")
    r = api.post("/api/interns", headers=sup, json={
        "first_name": "Conflict", "last_name": "Case",
        "ojt_start_date": str(MON), "required_hours": 486,
        "supervisor_id": api.ids["supervisor"], "status": "ACTIVE",
    })
    intern_id = r.json()["id"]

    shifts = {s["code"]: s for s in api.get("/api/shifts", headers=sup).json()}

    # 22:00-07:00 on Monday.
    r = api.post("/api/schedules/assignments", headers=sup, json={
        "intern_id": intern_id, "shift_template_id": shifts["SHIFT-16"]["id"],
        "assignment_type": "WEEKLY_RECURRING", "day_of_week": 0,
        "effective_from": str(MON),
    })
    assert r.status_code == 201

    # 23:00-08:00 also on Monday would overlap the same night.
    r = api.post("/api/schedules/validate", headers=sup, json={
        "intern_id": intern_id, "shift_template_id": shifts["SHIFT-17"]["id"],
        "assignment_type": "WEEKLY_RECURRING", "day_of_week": 0,
        "effective_from": str(MON),
    })
    assert r.status_code == 200
    result = r.json()
    assert result["is_valid"] is False
    assert any(e["code"] == "SCHEDULE_CONFLICT" for e in result["errors"])

    # Saving it is refused.
    r = api.post("/api/schedules/assignments", headers=sup, json={
        "intern_id": intern_id, "shift_template_id": shifts["SHIFT-17"]["id"],
        "assignment_type": "WEEKLY_RECURRING", "day_of_week": 0,
        "effective_from": str(MON),
    })
    assert r.status_code == 409


def test_system_status_self_check(api):
    headers = login(api, "admin")
    r = api.get("/api/admin/system-status", headers=headers)
    assert r.status_code == 200
    body = r.json()
    assert body["business_timezone"] == "Asia/Manila"
    checks = {c["check"]: c for c in body["checks"]}
    assert checks["Field encryption configured"]["ok"] is True
    assert checks["Shift templates seeded"]["ok"] is True
    assert checks["At least one active System Administrator"]["ok"] is True


def test_validation_errors_are_readable(api):
    headers = login(api, "sup")
    r = api.post("/api/interns", headers=headers, json={
        "first_name": "X", "last_name": "Y",
        "ojt_start_date": str(MON),
        "school_submission_deadline": str(MON - timedelta(days=10)),
    })
    assert r.status_code == 422
    body = r.json()
    assert "problems" in body
    assert any("cannot be before" in p["message"] for p in body["problems"])

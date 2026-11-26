"""Schools, the school-side coordinator, and intern self-registration.

The registration endpoint is the application's only unauthenticated write, so
most of what follows is about what it refuses rather than what it accepts.
"""
from __future__ import annotations

from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base, get_db
from app.core.enums import RoleName
from app.main import app
import app.models as m


@pytest.fixture
def api():
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

        def mkuser(username, name, role):
            u = m.User(
                username=username, email=f"{username}@test.local",
                password_hash=hash_password("TestPass12345"), full_name=name,
                department_id=dept.id,
            )
            u.sync_lookup_indexes()
            u.roles = [roles[role]]
            s.add(u)
            s.flush()
            return u

        mkuser("admin", "Administrator", RoleName.SYSTEM_ADMINISTRATOR)
        mkuser("mgr", "IT Manager", RoleName.IT_MANAGER)
        mkuser("ana", "Ana Cruz", RoleName.INTERN)

        school = m.School(
            name="Holy Angel University", short_name="HAU",
            coordinator_name="Prof. Juan Dela Cruz",
            coordinator_email="ojt@hau.edu.ph",
            coordinator_contact="045-888-0000",
            default_required_hours=486,
        )
        s.add(school)
        s.commit()
        ids = {"dept": dept.id, "school": school.id}

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


def valid_submission(**over) -> dict:
    payload = {
        "first_name": "Marco",
        "last_name": "Reyes",
        "email": "marco.reyes@student.hau.edu.ph",
        "contact_number": "0917-555-1234",
        "course": "BS Information Technology",
        "year_level": "4th year",
        "coordinator_name": "Prof. Juan Dela Cruz",
        "coordinator_email": "ojt@hau.edu.ph",
        "required_hours": 486,
    }
    payload.update(over)
    return payload


# --- Schools ---------------------------------------------------------------


def test_public_school_list_withholds_coordinator_details(api):
    """The open endpoint feeds a picker, so it must not leak staff addresses."""
    r = api.get("/api/schools/public")
    assert r.status_code == 200
    assert r.json(), "the seeded school should be listed"
    for entry in r.json():
        assert set(entry) == {"id", "name", "short_name"}
        assert "coordinator_email" not in entry


def test_school_list_requires_authentication(api):
    assert api.get("/api/schools").status_code == 401


def test_coordinator_details_visible_to_authorised_reader(api):
    r = api.get("/api/schools", headers=login(api, "mgr"))
    assert r.status_code == 200
    school = r.json()[0]
    assert school["coordinator_name"] == "Prof. Juan Dela Cruz"
    assert school["coordinator_email"] == "ojt@hau.edu.ph"


def test_duplicate_school_name_is_refused(api):
    """Two rows for one school would split its interns across both."""
    r = api.post(
        "/api/schools",
        headers=login(api, "mgr"),
        json={"name": "holy angel university"},
    )
    assert r.status_code == 409
    assert "already on file" in r.json()["detail"].lower()


def test_intern_exposes_resolved_school_and_coordinator(api):
    """A bare school_id is useless to the screens that need the name."""
    headers = login(api, "admin")
    created = api.post(
        "/api/interns",
        headers=headers,
        json={
            "first_name": "Ana", "last_name": "Cruz",
            "ojt_start_date": str(date(2026, 11, 2)),
            "required_hours": 486,
            "school_id": api.ids["school"],
            "department_id": api.ids["dept"],
        },
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["school_name"] == "Holy Angel University"
    assert body["school_short_name"] == "HAU"
    assert body["coordinator_name"] == "Prof. Juan Dela Cruz"
    assert body["coordinator_email"] == "ojt@hau.edu.ph"


def test_coordinator_named_at_enrolment_wins_over_the_school_record(api):
    """A school changing coordinator must not rewrite who signed off already.

    The name captured on the intern is what the existing placement was agreed
    with, so it takes precedence over whoever currently holds the role.
    """
    intern = m.Intern(
        first_name="Liza", last_name="Tan",
        ojt_start_date=date(2026, 11, 2), required_hours=486,
        school_coordinator="Engr. Perez (named at enrolment)",
    )
    intern.school = m.School(
        name="Another School", coordinator_name="Prof. Replacement",
        coordinator_email="new@school.edu",
    )
    assert intern.coordinator_name == "Engr. Perez (named at enrolment)"
    # The address still comes from the school: it is where post goes today.
    assert intern.coordinator_email == "new@school.edu"


# --- Registration: what it refuses -----------------------------------------


def test_submission_creates_no_intern_and_no_account(api):
    """An open endpoint must not be able to put anything into the live system."""
    r = api.post("/api/registrations", json=valid_submission(
        school_id=api.ids["school"]
    ))
    assert r.status_code == 202, r.text

    with api.SessionLocal() as s:
        assert s.scalar(select(m.Intern)) is None
        assert s.scalar(
            select(m.User).where(m.User.username.like("marco%"))
        ) is None
        reg = s.scalar(select(m.InternRegistration))
        assert reg.status == "PENDING"
        assert reg.created_intern_id is None


def test_acknowledgement_leaks_no_personal_data(api):
    """Echoing details back would make this a way to probe who is enrolled."""
    r = api.post("/api/registrations", json=valid_submission(
        school_id=api.ids["school"]
    ))
    body = r.json()
    assert set(body) == {"reference_code", "status", "message"}
    assert "marco" not in r.text.lower()


def test_reference_code_is_not_sequential(api):
    """A guessable code would expose every applicant's status."""
    codes = []
    for i in range(3):
        r = api.post("/api/registrations", json=valid_submission(
            school_id=api.ids["school"], email=f"a{i}@student.hau.edu.ph"
        ))
        codes.append(r.json()["reference_code"])
    assert len(set(codes)) == 3
    bodies = [c.rsplit("-", 1)[1] for c in codes]
    assert all(len(b) == 8 for b in bodies)
    # Sequential allocation would make these adjacent; random will not.
    assert len(set(bodies)) == 3


def test_applicant_cannot_set_privileged_fields(api):
    """Status and intern code are the reviewer's to set, not the applicant's."""
    r = api.post("/api/registrations", json=valid_submission(
        school_id=api.ids["school"],
        status="APPROVED",
        intern_code="OJT-2026-0001",
        created_intern_id=1,
    ))
    assert r.status_code == 202
    with api.SessionLocal() as s:
        reg = s.scalar(select(m.InternRegistration))
        assert reg.status == "PENDING"
        assert reg.created_intern_id is None


def test_school_is_required(api):
    r = api.post("/api/registrations", json=valid_submission())
    assert r.status_code == 422


def test_unknown_school_id_is_refused(api):
    r = api.post("/api/registrations", json=valid_submission(school_id=9999))
    assert r.status_code == 422


def test_typed_school_name_is_accepted_but_creates_no_school(api):
    """An open link must not be able to fill the schools list with junk."""
    r = api.post("/api/registrations", json=valid_submission(
        school_name_text="Some Unlisted College"
    ))
    assert r.status_code == 202
    with api.SessionLocal() as s:
        names = [x.name for x in s.scalars(select(m.School)).all()]
        assert names == ["Holy Angel University"]


def test_deadline_before_start_is_refused(api):
    r = api.post("/api/registrations", json=valid_submission(
        school_id=api.ids["school"],
        ojt_start_date="2026-12-01",
        school_submission_deadline="2026-11-01",
    ))
    assert r.status_code == 422


def test_coordinator_email_required_by_default(api):
    """The signed timesheet has to go somewhere when the placement ends."""
    payload = valid_submission(school_id=api.ids["school"])
    payload.pop("coordinator_email")
    r = api.post("/api/registrations", json=payload)
    assert r.status_code == 422
    assert "coordinator" in r.text.lower()


def test_registration_can_be_closed_between_intakes(api):
    headers = login(api, "admin")
    assert api.put(
        "/api/admin/settings/registration_open",
        headers=headers, json={"value": "false"},
    ).status_code == 200

    assert api.get("/api/registrations/open").json()["open"] is False
    r = api.post("/api/registrations", json=valid_submission(
        school_id=api.ids["school"]
    ))
    assert r.status_code == 403
    assert "closed" in r.json()["detail"].lower()


def test_submissions_are_capped_per_address(api):
    """Volume limit on an endpoint reachable without a credential."""
    headers = login(api, "admin")
    api.put(
        "/api/admin/settings/registration_max_per_ip_per_hour",
        headers=headers, json={"value": "2"},
    )
    for i in range(2):
        assert api.post("/api/registrations", json=valid_submission(
            school_id=api.ids["school"], email=f"ok{i}@student.hau.edu.ph"
        )).status_code == 202

    r = api.post("/api/registrations", json=valid_submission(
        school_id=api.ids["school"], email="third@student.hau.edu.ph"
    ))
    assert r.status_code == 429
    # The message has to be usable by a genuine group on one campus network.
    assert "later" in r.json()["detail"].lower()


# --- Registration: status check -------------------------------------------


def test_status_check_needs_the_exact_code(api):
    code = api.post("/api/registrations", json=valid_submission(
        school_id=api.ids["school"]
    )).json()["reference_code"]

    assert api.get(f"/api/registrations/status/{code}").status_code == 200
    assert api.get("/api/registrations/status/REG-2026-WRONGXX").status_code == 404


def test_status_check_returns_no_personal_data(api):
    code = api.post("/api/registrations", json=valid_submission(
        school_id=api.ids["school"]
    )).json()["reference_code"]
    r = api.get(f"/api/registrations/status/{code}")
    assert set(r.json()) == {
        "reference_code", "status", "submitted_on", "reviewed_on",
        "review_notes",
    }
    assert "marco" not in r.text.lower()


# --- Review ----------------------------------------------------------------


def test_review_queue_requires_permission(api):
    assert api.get("/api/registrations").status_code == 401
    # An intern has no business reading other applicants' submissions.
    assert api.get(
        "/api/registrations", headers=login(api, "ana")
    ).status_code == 403


def test_approval_creates_intern_and_one_time_account(api):
    headers = login(api, "mgr")
    api.post("/api/registrations", json=valid_submission(
        school_id=api.ids["school"]
    ))
    reg_id = api.get("/api/registrations", headers=headers).json()["items"][0]["id"]

    r = api.post(
        f"/api/registrations/{reg_id}/approve",
        headers=headers,
        json={"create_user_account": True, "department_id": api.ids["dept"]},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["intern_code"].startswith("OJT-")
    assert body["username"]
    assert body["temporary_password"]

    with api.SessionLocal() as s:
        intern = s.get(m.Intern, body["intern_id"])
        assert intern.status == "ACTIVE"
        assert intern.school_id == api.ids["school"]
        # Required hours fall back to the school's default.
        assert intern.required_hours == 486
        assert intern.school_coordinator == "Prof. Juan Dela Cruz"
        account = s.get(m.User, intern.user_id)
        # A temporary password must be replaced before anything is reachable.
        assert account.must_change_password is True
        assert RoleName.INTERN in account.role_names


def test_the_registration_record_is_kept_as_submitted(api):
    """It is the evidence of what the applicant actually typed."""
    headers = login(api, "mgr")
    api.post("/api/registrations", json=valid_submission(
        school_id=api.ids["school"], required_hours=300
    ))
    reg_id = api.get("/api/registrations", headers=headers).json()["items"][0]["id"]

    # The reviewer corrects the hours upward.
    api.post(
        f"/api/registrations/{reg_id}/approve",
        headers=headers, json={"required_hours": 486},
    )
    with api.SessionLocal() as s:
        reg = s.get(m.InternRegistration, reg_id)
        assert reg.required_hours == 300, "the submission must not be edited"
        assert s.get(m.Intern, reg.created_intern_id).required_hours == 486


def test_approving_twice_is_refused(api):
    headers = login(api, "mgr")
    api.post("/api/registrations", json=valid_submission(
        school_id=api.ids["school"]
    ))
    reg_id = api.get("/api/registrations", headers=headers).json()["items"][0]["id"]

    assert api.post(
        f"/api/registrations/{reg_id}/approve", headers=headers, json={}
    ).status_code == 200
    second = api.post(
        f"/api/registrations/{reg_id}/approve", headers=headers, json={}
    )
    assert second.status_code == 409
    with api.SessionLocal() as s:
        assert len(s.scalars(select(m.Intern)).all()) == 1


def test_approval_blocked_when_the_address_already_belongs_to_an_intern(api):
    """A second record would split the intern's hours across two ledgers."""
    headers = login(api, "mgr")
    api.post("/api/registrations", json=valid_submission(
        school_id=api.ids["school"]
    ))
    first = api.get("/api/registrations", headers=headers).json()["items"][0]["id"]
    api.post(f"/api/registrations/{first}/approve", headers=headers, json={})

    api.post("/api/registrations", json=valid_submission(
        school_id=api.ids["school"], first_name="Marco", last_name="Reyes"
    ))
    pending = api.get("/api/registrations", headers=headers).json()["items"]
    assert pending, "the second submission should still be pending"
    dupe = pending[0]
    # Flagged for the reviewer before they act, not only on failure.
    assert dupe["duplicate_of_intern_id"] is not None

    r = api.post(f"/api/registrations/{dupe['id']}/approve", headers=headers, json={})
    assert r.status_code == 409
    assert "already exists" in r.json()["detail"].lower()


def test_approval_requires_a_resolved_school(api):
    """A typed school name must be matched, not guessed at."""
    headers = login(api, "mgr")
    api.post("/api/registrations", json=valid_submission(
        school_name_text="Unlisted College"
    ))
    reg_id = api.get("/api/registrations", headers=headers).json()["items"][0]["id"]
    r = api.post(f"/api/registrations/{reg_id}/approve", headers=headers, json={})
    assert r.status_code == 422
    assert "unlisted college" in r.json()["detail"].lower()


def test_rejection_requires_a_reason_and_reaches_the_applicant(api):
    headers = login(api, "mgr")
    code = api.post("/api/registrations", json=valid_submission(
        school_id=api.ids["school"]
    )).json()["reference_code"]
    reg_id = api.get("/api/registrations", headers=headers).json()["items"][0]["id"]

    assert api.post(
        f"/api/registrations/{reg_id}/reject", headers=headers, json={}
    ).status_code == 422

    assert api.post(
        f"/api/registrations/{reg_id}/reject",
        headers=headers,
        json={"review_notes": "Duplicate of REG-2026-EARLIER."},
    ).status_code == 200

    status_body = api.get(f"/api/registrations/status/{code}").json()
    assert status_body["status"] == "REJECTED"
    assert "Duplicate" in status_body["review_notes"]


# --- Login tracking --------------------------------------------------------


def test_failed_sign_in_is_audited(api):
    """PCI-DSS 10.2.4: invalid access attempts have to be logged.

    The lockout entry alone is not enough - it says five attempts happened,
    not when or from where each one came.
    """
    api.post("/api/auth/login", json={"username": "mgr", "password": "wrong"})
    r = api.get(
        "/api/admin/audit",
        headers=login(api, "admin"),
        params={"action": "LOGIN_FAILED"},
    )
    assert r.status_code == 200
    rows = r.json()
    assert len(rows) == 1
    assert rows[0]["actor"] == "mgr"
    assert "1 of 5" in rows[0]["summary"]


def test_audit_filter_accepts_several_actions(api):
    """The login tracker needs one timeline, not three it has to merge."""
    api.post("/api/auth/login", json={"username": "mgr", "password": "wrong"})
    headers = login(api, "admin")

    r = api.get(
        "/api/admin/audit",
        headers=headers,
        params={"action": "LOGIN,LOGIN_FAILED"},
    )
    actions = {row["action"] for row in r.json()}
    assert actions == {"LOGIN", "LOGIN_FAILED"}


def test_audit_entries_expose_the_client(api):
    """IP and user agent are what identify a session in a sign-in log."""
    api.post(
        "/api/auth/login",
        json={"username": "mgr", "password": "TestPass12345"},
        headers={"User-Agent": "Mozilla/5.0 TestRunner"},
    )
    r = api.get(
        "/api/admin/audit", headers=login(api, "admin"),
        params={"action": "LOGIN"},
    )
    entry = next(x for x in r.json() if x["actor"] == "mgr")
    assert entry["ip_address"]
    assert "TestRunner" in entry["user_agent"]


def test_login_tracker_is_not_readable_without_audit_permission(api):
    assert api.get(
        "/api/admin/audit", headers=login(api, "ana"),
        params={"action": "LOGIN"},
    ).status_code == 403


def test_boolean_setting_sent_as_a_string_is_parsed_not_truthiness_tested(api):
    """"false" must turn a rule off.

    Every non-empty string is truthy in Python, so a naive implementation
    stores `true` for "false" and silently enables the setting. The web UI
    sends real booleans, but the endpoint accepts any JSON value, so a script
    or curl call reaches this path.
    """
    headers = login(api, "admin")
    for sent, expected in (
        ("false", False), ("true", True), ("0", False), ("no", False),
        ("ON", True), (False, False), (True, True),
    ):
        r = api.put(
            "/api/admin/settings/registration_open",
            headers=headers, json={"value": sent},
        )
        assert r.status_code == 200, (sent, r.text)
        assert api.get("/api/registrations/open").json()["open"] is expected, (
            f"sending {sent!r} should give {expected}"
        )


def test_unparseable_boolean_setting_is_rejected_rather_than_guessed(api):
    r = api.put(
        "/api/admin/settings/registration_open",
        headers=login(api, "admin"), json={"value": "maybe"},
    )
    assert r.status_code == 400
    assert "boolean" in r.json()["detail"].lower()


# --- Purging demo data -----------------------------------------------------


def test_purge_demo_removes_the_demo_accounts(db):
    """The demo accounts share one published password.

    Leaving them behind is the failure that matters: the operator believes the
    system is clean and goes live, while a documented password still signs in
    as an IT Manager. An earlier version of the purge removed interns and
    attendance but kept every account, school and team.
    """
    from app.core.security import verify_password
    from app.seed import DEMO_PASSWORD, create_admin, purge_demo, seed_demo
    from sqlalchemy import func, select

    create_admin(db, password="RealAdminPass123")
    seed_demo(db)
    db.flush()

    assert db.scalar(select(func.count()).select_from(m.User)) > 1
    purge_demo(db)
    db.flush()

    remaining = db.scalars(select(m.User)).all()
    assert [u.username for u in remaining] == ["admin"]
    assert not any(
        verify_password(DEMO_PASSWORD, u.password_hash) for u in remaining
    ), "the published demo password must not sign anyone in after a purge"


def test_purge_demo_leaves_nothing_behind(db):
    from app.seed import create_admin, purge_demo, seed_demo
    from sqlalchemy import func, select

    create_admin(db, password="RealAdminPass123")
    seed_demo(db)
    db.flush()
    purge_demo(db)
    db.flush()

    for model in (
        m.Intern, m.School, m.Team, m.Department, m.Supervisor, m.Mentor,
        m.AttendanceRecord, m.OJTHourLedger, m.ExposureLog,
        m.InternEvaluation, m.LeaveRequest, m.ScheduleAssignment,
        m.InternRegistration,
    ):
        count = db.scalar(select(func.count()).select_from(model))
        assert count == 0, f"{model.__name__} still has {count} demo row(s)"


def test_purge_demo_keeps_configuration_and_shift_templates(db):
    """Those are not demo data: they are what the system needs to work."""
    from app.seed import create_admin, purge_demo, seed_demo
    from sqlalchemy import func, select

    create_admin(db, password="RealAdminPass123")
    seed_demo(db)
    db.flush()
    purge_demo(db)
    db.flush()

    assert db.scalar(select(func.count()).select_from(m.ShiftTemplate)) == 17
    assert db.scalar(select(func.count()).select_from(m.SystemSetting)) >= 36


def test_purge_demo_spares_real_records(db):
    """A real intern entered alongside the demo must survive the purge."""
    from app.seed import create_admin, purge_demo, seed_demo
    from sqlalchemy import select

    create_admin(db, password="RealAdminPass123")
    seed_demo(db)
    real_school = m.School(name="A Real Partner School")
    db.add(real_school)
    db.flush()
    real = m.Intern(
        intern_code="OJT-2026-9999", first_name="Real", last_name="Person",
        ojt_start_date=date(2026, 11, 2), required_hours=486,
        school_id=real_school.id, remarks="Entered by the IT Supervisor",
    )
    db.add(real)
    db.flush()

    purge_demo(db)
    db.flush()

    survivors = db.scalars(select(m.Intern)).all()
    assert [i.intern_code for i in survivors] == ["OJT-2026-9999"]
    assert db.scalar(
        select(m.School).where(m.School.name == "A Real Partner School")
    ) is not None


def test_purge_demo_is_safe_to_run_twice(db):
    from app.seed import create_admin, purge_demo, seed_demo

    create_admin(db, password="RealAdminPass123")
    seed_demo(db)
    db.flush()
    purge_demo(db)
    db.flush()
    assert purge_demo(db) == {}, "a second purge should find nothing to do"

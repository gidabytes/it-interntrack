"""Demo data seeder.

Everything created here is clearly marked DEMO DATA: intern remarks, the
school name and user accounts all say so, and ``purge_demo`` removes exactly
what this module created. Never run against a live database you intend to keep.
"""
from __future__ import annotations

import random
from datetime import date, time, timedelta

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.enums import (
    ApprovalStatus, ExposureArea, InternStatus, LeaveType, ProficiencyLevel,
    RoleName, TechnicalFunction,
)
from app.core.permissions import ROLE_DISPLAY, ROLE_PERMISSIONS
from app.core.security import hash_password
from app.core.timeutil import combine_business
from app.models.identity import Permission, Role, User
from app.models.intern import Intern
from app.models.leave import LeaveRequest
from app.models.org import Department, Mentor, School, Supervisor, Team
from app.models.schedule import Holiday, RestDay, ScheduleAssignment, ShiftTemplate
from app.models.technical import ExposureLog, InternEvaluation, TechnicalAssignment
from app.services import attendance_service, exposure_service, ojt_engine
from app.services.settings_service import ensure_defaults, load_rules
from app.services.shift_service import ensure_standard_shifts

DEMO_MARKER = "DEMO DATA"
DEMO_PASSWORD = "InternTrack#2026"


def seed_rbac(db: Session) -> dict[str, Role]:
    """Create permissions and roles. Idempotent."""
    existing_perms = {p.code: p for p in db.scalars(select(Permission)).all()}
    from app.core.permissions import PERMISSIONS

    for code, desc in PERMISSIONS.items():
        if code not in existing_perms:
            p = Permission(code=code, description=desc)
            db.add(p)
            existing_perms[code] = p
    db.flush()

    roles: dict[str, Role] = {
        r.name: r for r in db.scalars(select(Role)).all()
    }
    for name, perms in ROLE_PERMISSIONS.items():
        role = roles.get(name)
        if role is None:
            role = Role(
                name=name,
                display_name=ROLE_DISPLAY.get(name, name),
                description=f"{ROLE_DISPLAY.get(name, name)} role",
            )
            db.add(role)
            db.flush()
            roles[name] = role
        role.permissions = [existing_perms[c] for c in sorted(perms)]
    db.flush()
    return roles


def create_admin(
    db: Session,
    *,
    username: str = "admin",
    password: str | None = None,
    full_name: str = "System Administrator",
    email: str = "admin@interntrack.local",
) -> User:
    """Create the bootstrap administrator if no administrator exists."""
    roles = seed_rbac(db)
    existing = db.scalar(select(User).where(User.username == username))
    if existing:
        return existing
    user = User(
        username=username,
        email=email,
        full_name=full_name,
        password_hash=hash_password(password or DEMO_PASSWORD),
        position_title="System Administrator",
        must_change_password=True,
    )
    user.sync_lookup_indexes()
    user.roles = [roles[RoleName.SYSTEM_ADMINISTRATOR]]
    db.add(user)
    db.flush()
    return user


def seed_demo(db: Session, *, today: date | None = None) -> dict:
    """Build a realistic 24/7 IT department with six weeks of history."""
    today = today or date.today()
    ensure_defaults(db)
    ensure_standard_shifts(db)
    roles = seed_rbac(db)
    rules = load_rules(db)
    rng = random.Random(20261120)  # deterministic demo data

    # --- Organisation ------------------------------------------------------
    dept = db.scalar(select(Department).where(Department.code == "ITD-DGT"))
    if dept is None:
        dept = Department(
            code="ITD-DGT", name="Information Technology Department",
            site_code="DGT", remarks=DEMO_MARKER,
        )
        db.add(dept)
        db.flush()

    team_specs = [
        ("SD", "Service Desk", TechnicalFunction.SERVICE_DESK),
        ("DS", "Desktop Support", TechnicalFunction.DESKTOP_SUPPORT),
        ("NOC", "Network Operations", TechnicalFunction.NETWORK_OPERATIONS),
        ("INF", "Infrastructure", TechnicalFunction.INFRASTRUCTURE),
        ("SEC", "Cybersecurity", TechnicalFunction.CYBERSECURITY),
    ]
    teams: dict[str, Team] = {}
    for code, name, fn in team_specs:
        t = db.scalar(select(Team).where(Team.code == code, Team.department_id == dept.id))
        if t is None:
            t = Team(
                code=code, name=name, department_id=dept.id,
                technical_function=fn, remarks=DEMO_MARKER,
            )
            db.add(t)
            db.flush()
        teams[code] = t

    # Coordinator details are filled in for two of the three schools and left
    # off the third on purpose: a missing coordinator email is the realistic
    # case the Schools screen and the intern record both have to flag, since
    # the signed timesheet has nowhere to go without one.
    school_specs = [
        (
            "Polytechnic University of the Philippines", "PUP", 486,
            "Prof. Juan Dela Cruz", "ojt@pup.demo", "02-5335-1787",
            "Anonas St, Sta. Mesa, Manila",
        ),
        (
            "Technological Institute of the Philippines", "TIP", 500,
            "Engr. Maria Santos", "ojt@tip.demo", "02-8911-0964",
            "938 Aurora Blvd, Cubao, Quezon City",
        ),
        ("STI College", "STI", 400, None, None, None, None),
    ]
    schools: dict[str, School] = {}
    for name, short, hours, coord, email, contact, address in school_specs:
        s = db.scalar(select(School).where(School.name == name))
        if s is None:
            s = School(
                name=name, short_name=short, default_required_hours=hours,
                coordinator_name=coord,
                coordinator_email=email,
                coordinator_contact=contact,
                address=address,
                remarks=DEMO_MARKER,
            )
            db.add(s)
            db.flush()
        schools[short] = s

    # --- People ------------------------------------------------------------
    admin = create_admin(db)

    def make_user(username, full_name, role, position, email=None) -> User:
        u = db.scalar(select(User).where(User.username == username))
        if u:
            return u
        u = User(
            username=username,
            email=email or f"{username}@interntrack.local",
            full_name=full_name,
            password_hash=hash_password(DEMO_PASSWORD),
            position_title=position,
            department_id=dept.id,
            must_change_password=False,
            # Marked so purge-demo can find and remove this account. Without
            # it the account is indistinguishable from a real one and survives
            # the purge, leaving the published demo password working after
            # go-live.
            remarks=DEMO_MARKER,
        )
        u.sync_lookup_indexes()
        u.roles = [roles[role]]
        db.add(u)
        db.flush()
        return u

    manager = make_user(
        "itmanager", "Gregorio Antonio", RoleName.IT_MANAGER, "IT Manager"
    )
    sup_users = [
        make_user("rsantos", "Reymond Santos", RoleName.IT_SUPERVISOR, "IT Supervisor"),
        make_user("mlim", "Marco Lim", RoleName.IT_SUPERVISOR, "IT Supervisor"),
    ]
    mentor_users = [
        make_user("jvaldez", "Jenny Valdez", RoleName.IT_MENTOR, "Senior Systems Engineer"),
        make_user("pcruz", "Paolo Cruz", RoleName.IT_MENTOR, "Network Engineer"),
    ]

    supervisors: list[Supervisor] = []
    for u, team_code in zip(sup_users, ["SD", "NOC"]):
        s = db.scalar(select(Supervisor).where(Supervisor.user_id == u.id))
        if s is None:
            s = Supervisor(
                user_id=u.id, department_id=dept.id, team_id=teams[team_code].id,
                max_interns=12, remarks=DEMO_MARKER,
            )
            db.add(s)
            db.flush()
        supervisors.append(s)

    mentors: list[Mentor] = []
    for u, team_code, spec in zip(
        mentor_users, ["INF", "NOC"], ["Windows Server / AD", "Routing & Switching"]
    ):
        mt = db.scalar(select(Mentor).where(Mentor.user_id == u.id))
        if mt is None:
            mt = Mentor(
                user_id=u.id, team_id=teams[team_code].id, specialization=spec,
                remarks=DEMO_MARKER,
            )
            db.add(mt)
            db.flush()
        mentors.append(mt)

    # --- Holidays ----------------------------------------------------------
    for d, name in [
        (date(today.year, 11, 30), "Bonifacio Day"),
        (date(today.year, 12, 25), "Christmas Day"),
        (date(today.year, 12, 30), "Rizal Day"),
    ]:
        if not db.scalar(select(Holiday).where(Holiday.holiday_date == d)):
            db.add(Holiday(
                holiday_date=d, name=name, holiday_type="REGULAR",
                is_non_working=True, is_creditable=False, remarks=DEMO_MARKER,
            ))
    db.flush()

    shifts = {
        t.code: t for t in db.scalars(select(ShiftTemplate)).all()
    }

    # --- Interns -----------------------------------------------------------
    # Mix of shifts, schools, paces and risk profiles so every dashboard and
    # report has something meaningful to show.
    intern_specs = [
        # name, school, team, shift, workdays, start offset, required, deadline offset, pace
        ("Ana Marie|Cruz", "PUP", "SD", "SHIFT-02", (0,1,2,3,4), -45, 486, 130, 1.00),
        ("Benjamin|Reyes", "PUP", "SD", "SHIFT-02", (0,1,2,3,4), -45, 486, 130, 0.78),
        ("Carla|Dizon", "TIP", "NOC", "SHIFT-16", (0,1,2,3,4), -40, 500, 120, 1.00),
        ("Daniel|Mercado", "TIP", "NOC", "SHIFT-16", (1,2,3,4,5), -40, 500,  38, 0.62),
        ("Elena|Villanueva", "STI", "DS", "SHIFT-01", (0,1,2,3,4), -38, 400, 110, 0.95),
        ("Francis|Gomez", "STI", "DS", "SHIFT-08", (2,3,4,5,6), -30, 400, 110, 0.88),
        ("Grace|Pineda", "PUP", "INF", "SHIFT-03", (0,1,2,3,4), -28, 486, 150, 1.00),
        ("Hector|Ramos", "TIP", "SEC", "SHIFT-17", (0,1,2,3,4), -25, 500, 160, 0.70),
        ("Irene|Bautista", "STI", "SD", "SHIFT-07", (1,2,3,4,5), -20, 400, 140, 0.92),
        ("Jerome|Castillo", "PUP", "NOC", "SHIFT-10", (0,2,4,5,6), -14, 486, 170, 0.85),
        ("Karla|Navarro", "TIP", "INF", "SHIFT-04", (0,1,2,3,4),  -7, 500, 180, 1.00),
        ("Luis|Fernandez", "STI", "SEC", "SHIFT-09", (0,1,2,3,4),  -3, 400, 185, 1.00),
    ]

    created_interns: list[Intern] = []
    for idx, (name, school, team, shift, dows, start_off, required,
              deadline_off, pace) in enumerate(intern_specs):
        first, last = name.split("|")
        code_year = (today + timedelta(days=start_off)).year
        existing = db.scalar(
            select(Intern).where(
                Intern.first_name == first, Intern.last_name == last
            )
        )
        if existing:
            created_interns.append(existing)
            continue

        start = today + timedelta(days=start_off)
        intern = Intern(
            intern_code=f"OJT-{code_year}-{idx + 1:04d}",
            first_name=first,
            last_name=last,
            contact_number=f"0917-555-{1000 + idx:04d}",
            email=f"{first.split()[0].lower()}.{last.lower()}@{school.lower()}.demo",
            emergency_contact_name=f"Guardian of {first}",
            emergency_contact_number=f"0918-444-{2000 + idx:04d}",
            emergency_contact_relation="Parent",
            school_id=schools[school].id,
            course="BS Information Technology" if idx % 2 == 0
            else "BS Computer Science",
            year_level="4th Year",
            ojt_start_date=start,
            expected_completion_date=start + timedelta(days=120),
            school_submission_deadline=today + timedelta(days=deadline_off),
            required_hours=required,
            department_id=dept.id,
            team_id=teams[team].id,
            supervisor_id=supervisors[idx % len(supervisors)].id,
            mentor_id=mentors[idx % len(mentors)].id,
            primary_technical_function=teams[team].technical_function,
            status=InternStatus.ACTIVE,
            remarks=f"{DEMO_MARKER} - generated for demonstration",
        )
        intern.sync_lookup_indexes()
        db.add(intern)
        db.flush()

        # Login account for the intern.
        iu = make_user(
            f"{first.split()[0].lower()}.{last.lower()}",
            f"{first} {last}",
            RoleName.INTERN,
            "OJT Intern",
            email=intern.email,
        )
        intern.user_id = iu.id

        # Schedule: working days on the assigned shift, the rest as rest days.
        for dow in dows:
            db.add(ScheduleAssignment(
                intern_id=intern.id,
                shift_template_id=shifts[shift].id,
                assignment_type="WEEKLY_RECURRING",
                day_of_week=dow,
                effective_from=start,
                approval_status=ApprovalStatus.APPROVED,
                approved_by_id=supervisors[idx % len(supervisors)].user_id,
                remarks=DEMO_MARKER,
            ))
        for dow in set(range(7)) - set(dows):
            db.add(RestDay(
                intern_id=intern.id, day_of_week=dow, effective_from=start,
                remarks=DEMO_MARKER,
            ))

        db.add(TechnicalAssignment(
            intern_id=intern.id,
            technical_function=teams[team].technical_function,
            team_id=teams[team].id,
            date_from=start,
            is_primary=True,
            objectives=f"{DEMO_MARKER} - rotation through {teams[team].name}",
        ))
        db.flush()
        created_interns.append(intern)

    db.flush()

    # --- Attendance history -------------------------------------------------
    tpl_by_intern = {}
    for intern in created_interns:
        a = db.scalar(
            select(ScheduleAssignment).where(
                ScheduleAssignment.intern_id == intern.id,
                ScheduleAssignment.shift_template_id.is_not(None),
            )
        )
        tpl_by_intern[intern.id] = db.get(ShiftTemplate, a.shift_template_id) if a else None

    stats = {"records": 0, "absent": 0, "late": 0, "overtime": 0, "pending": 0}
    evaluation_time = combine_business(today, time(23, 59))

    for spec, intern in zip(intern_specs, created_interns):
        pace = spec[8]
        tpl = tpl_by_intern.get(intern.id)
        if tpl is None:
            continue
        sup_user_id = db.get(Supervisor, intern.supervisor_id).user_id

        d = intern.ojt_start_date
        while d < today:
            resolved_dows = spec[4]
            if d.weekday() not in resolved_dows:
                d += timedelta(days=1)
                continue
            if db.scalar(select(Holiday).where(Holiday.holiday_date == d)):
                d += timedelta(days=1)
                continue

            roll = rng.random()
            # pace drives how often a day is actually worked in full.
            if roll > pace:
                # Missed day: leave it with no punches so it finalises ABSENT.
                attendance_service.ensure_day_records(
                    db, intern, d, rules=rules, evaluation_time=evaluation_time
                )
                stats["absent"] += 1
                d += timedelta(days=1)
                continue

            late_minutes = 0
            if rng.random() < 0.12:
                late_minutes = rng.choice([8, 17, 25, 40])
            early_out = 0
            if rng.random() < 0.08:
                early_out = rng.choice([30, 55, 90])
            ot_minutes = 0
            if rng.random() < 0.10:
                ot_minutes = rng.choice([30, 60, 90])

            start_dt = combine_business(d, tpl.start_time) + timedelta(
                minutes=late_minutes
            )
            end_dt = (
                combine_business(d, tpl.start_time)
                + timedelta(minutes=tpl.elapsed_minutes - early_out + ot_minutes)
            )

            rec = attendance_service.manual_entry(
                db, intern, d,
                time_in_at=start_dt, time_out_at=end_dt,
                actor_user_id=sup_user_id,
                overtime_approved=bool(ot_minutes) and rng.random() < 0.7,
                rules=rules,
            )
            rec.source = "DIGITAL_TIMEKEEPING"

            # Most history is approved; the last few days stay pending so the
            # approval backlog and "pending hours" KPIs are not empty.
            if (today - d).days > 3:
                rec.approval_status = ApprovalStatus.APPROVED
                rec.approved_by_id = sup_user_id
                rec.approved_at = evaluation_time
            else:
                stats["pending"] += 1

            ojt_engine.post_attendance(
                db, rec, actor_user_id=sup_user_id, rules=rules
            )
            stats["records"] += 1
            if rec.is_late:
                stats["late"] += 1
            if rec.approved_overtime_minutes:
                stats["overtime"] += 1
            d += timedelta(days=1)
        db.flush()

    # --- Leave requests -----------------------------------------------------
    for intern in created_interns[:4]:
        start = today + timedelta(days=rng.choice([3, 6, 9]))
        if db.scalar(
            select(LeaveRequest).where(LeaveRequest.intern_id == intern.id)
        ):
            continue
        db.add(LeaveRequest(
            intern_id=intern.id,
            leave_type=rng.choice([
                LeaveType.SICK, LeaveType.SCHOOL_ACTIVITY, LeaveType.PERSONAL
            ]),
            date_from=start,
            date_to=start + timedelta(days=rng.choice([0, 1])),
            reason=f"{DEMO_MARKER} - sample leave request",
            requested_by_id=intern.user_id,
            approval_status=ApprovalStatus.PENDING,
        ))
    db.flush()

    # --- Technical exposure -------------------------------------------------
    activity_bank = {
        ExposureArea.DESKTOP_TROUBLESHOOTING: [
            "Reimaged agent workstations", "Resolved Outlook profile corruption",
            "Replaced failing SSD and restored user profile",
        ],
        ExposureArea.AD_ADMINISTRATION: [
            "Bulk user provisioning for new hire batch",
            "Group policy update for USB restriction",
            "Password reset workflow and account unlocks",
        ],
        ExposureArea.NETWORKING: [
            "VLAN change on access switches", "Patch panel tracing and labelling",
            "Wireless AP survey and channel adjustment",
        ],
        ExposureArea.HARDWARE_DEPLOYMENT: [
            "Deployed 20 workstations for new campaign",
            "Headset and peripheral rollout",
        ],
        ExposureArea.PATCHING: [
            "Monthly patch cycle verification", "Endpoint agent remediation",
        ],
        ExposureArea.DOCUMENTATION: [
            "Updated runbook for shift handover",
            "Documented network closet inventory",
        ],
        ExposureArea.PROJECTS: [
            "Assisted on seat map refresh project",
            "Supported floor expansion cabling project",
        ],
        ExposureArea.SERVICE_DESK_HANDLING: [
            "First-line ticket triage", "Escalation handling during peak volume",
        ],
    }
    for intern in created_interns:
        if db.scalar(select(ExposureLog).where(ExposureLog.intern_id == intern.id)):
            continue
        # Earlier interns have broader exposure; later ones show visible gaps.
        area_count = max(2, min(7, (today - intern.ojt_start_date).days // 7))
        areas = rng.sample(list(activity_bank), k=min(area_count, len(activity_bank)))
        mentor_user_id = db.get(Mentor, intern.mentor_id).user_id
        for area in areas:
            for _ in range(rng.randint(1, 3)):
                offset = rng.randint(1, max(2, (today - intern.ojt_start_date).days - 1))
                adate = intern.ojt_start_date + timedelta(days=offset)
                if adate >= today:
                    continue
                entry = ExposureLog(
                    intern_id=intern.id,
                    activity_date=adate,
                    exposure_area=area,
                    activity_title=rng.choice(activity_bank[area]),
                    description=f"{DEMO_MARKER} - sample activity record",
                    minutes_spent=rng.choice([120, 180, 240, 300, 420]),
                    proficiency_level=rng.choice(list(ProficiencyLevel)),
                    reference_ticket=f"INC-{rng.randint(40000, 49999)}",
                    logged_by_id=mentor_user_id,
                    verified_by_id=mentor_user_id if rng.random() < 0.6 else None,
                )
                if entry.verified_by_id:
                    entry.verified_at = combine_business(adate, time(17, 0))
                db.add(entry)
    db.flush()

    # --- Evaluations --------------------------------------------------------
    for intern in created_interns[:6]:
        if db.scalar(
            select(InternEvaluation).where(InternEvaluation.intern_id == intern.id)
        ):
            continue
        period_end = today - timedelta(days=today.weekday() + 1)
        db.add(InternEvaluation(
            intern_id=intern.id,
            period_start=period_end - timedelta(days=6),
            period_end=period_end,
            evaluation_type="WEEKLY",
            technical_skill_score=rng.randint(3, 5),
            attendance_score=rng.randint(3, 5),
            attitude_score=rng.randint(4, 5),
            initiative_score=rng.randint(3, 5),
            communication_score=rng.randint(3, 5),
            skills_acquired=f"{DEMO_MARKER} - sample evaluation",
            accomplishments="Handled assigned tickets within SLA.",
            development_areas="Continue building confidence on escalations.",
            evaluator_id=db.get(Supervisor, intern.supervisor_id).user_id,
            evaluator_role=RoleName.IT_SUPERVISOR,
        ))
    db.flush()

    # --- Pending registrations ---------------------------------------------
    # Three submissions covering the cases a reviewer actually has to handle:
    # a clean one, one naming a school that is not on file, and one that
    # duplicates an existing intern's address.
    from app.models.registration import InternRegistration, RegistrationStatus

    registration_specs = [
        {
            "first_name": "Marco", "middle_name": "L.", "last_name": "Reyes",
            "email": "marco.reyes@pup.demo",
            "contact_number": "0917-555-0101",
            "school_id": schools["PUP"].id,
            "course": "BS Information Technology", "year_level": "4th year",
            "coordinator_name": "Prof. Juan Dela Cruz",
            "coordinator_email": "ojt@pup.demo",
            "coordinator_contact": "02-5335-1787",
            "required_hours": 486,
            "primary_technical_function": "Service Desk",
            "emergency_contact_name": "Elena Reyes",
            "emergency_contact_number": "0917-555-0102",
            "emergency_contact_relation": "Mother",
            "applicant_notes": f"{DEMO_MARKER} - available for night shift.",
        },
        {
            "first_name": "Divina", "last_name": "Macaraeg",
            "email": "divina.macaraeg@unlisted.demo",
            "contact_number": "0918-555-0303",
            # No school_id: the reviewer has to add the school or match it.
            "school_name_text": "Pampanga State Agricultural University",
            "course": "BS Computer Engineering", "year_level": "3rd year",
            "coordinator_name": "Dr. Ramon Bautista",
            "coordinator_email": "ojt@psau.demo",
            "required_hours": 400,
            "primary_technical_function": "Network Operations",
            "applicant_notes": f"{DEMO_MARKER} - school not in the list.",
        },
    ]
    registrations = []
    for spec in registration_specs:
        reg = InternRegistration(
            reference_code=f"REG-{today.year}-DEMO{len(registrations) + 1:04d}",
            status=RegistrationStatus.PENDING,
            submitted_ip="203.0.113.10",
            submitted_user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/131.0"
            ),
            **spec,
        )
        reg.refresh_email_index()
        db.add(reg)
        registrations.append(reg)

    # The duplicate: same address as an intern already on file, so the review
    # screen flags it before it can become a second hour ledger.
    if created_interns:
        existing = created_interns[0]
        dupe = InternRegistration(
            reference_code=f"REG-{today.year}-DEMO0003",
            first_name=existing.first_name, last_name=existing.last_name,
            email=existing.email or "duplicate@pup.demo",
            school_id=existing.school_id,
            course="BS Information Technology",
            coordinator_email="ojt@pup.demo",
            status=RegistrationStatus.PENDING,
            submitted_ip="203.0.113.11",
            applicant_notes=f"{DEMO_MARKER} - duplicate of an existing intern.",
        )
        dupe.refresh_email_index()
        db.add(dupe)
        registrations.append(dupe)
    db.flush()

    return {
        "marker": DEMO_MARKER,
        "demo_password": DEMO_PASSWORD,
        "pending_registrations": len(registrations),
        "department": dept.code,
        "teams": len(teams),
        "schools": len(schools),
        "interns": len(created_interns),
        "supervisors": len(supervisors),
        "mentors": len(mentors),
        "shift_templates": len(shifts),
        "attendance": stats,
        "accounts": {
            "administrator": "admin",
            "it_manager": manager.username,
            "supervisors": [u.username for u in sup_users],
            "mentors": [u.username for u in mentor_users],
            "sample_intern": created_interns[0].user.username
            if created_interns and created_interns[0].user else None,
        },
    }


def purge_demo(db: Session) -> dict:
    """Remove every row the demo created.

    Matches on the DEMO DATA marker only, so anything entered by a real user
    is left alone, as is the bootstrap administrator - that account is created
    by ``create_admin`` and carries no marker.

    Deletion is ordered by dependency rather than by table, because several
    foreign keys to ``users`` are RESTRICT: an exposure log or evaluation has
    to go before the account that wrote it. If a demo account is referenced by
    a record a real user created, the database refuses the delete and this
    reports the account instead of leaving the data half-removed.
    """
    from app.models.attendance import AttendanceAdjustment, AttendanceRecord
    from app.models.leave import LeaveRequest
    from app.models.ojt import OJTHourLedger, OJTProgressSnapshot
    from app.models.registration import InternRegistration
    from app.models.technical import (
        ExposureLog, InternEvaluation, TechnicalAssignment,
    )
    from app.models.timesheet import Timesheet

    marker = f"{DEMO_MARKER}%"
    counts: dict[str, int] = {}

    def wipe(label: str, rows) -> None:
        n = 0
        for row in rows:
            db.delete(row)
            n += 1
        if n:
            counts[label] = counts.get(label, 0) + n

    # 1. Registrations first: they reference schools, interns and users, and
    #    nothing references them.
    wipe("registrations", db.scalars(
        select(InternRegistration).where(
            InternRegistration.applicant_notes.like(marker)
        )
    ).all())

    # 2. Interns and everything hanging off them. Most of these cascade, but
    #    they are removed explicitly so the report is accurate and so the
    #    order holds on a database where cascade is not enforced.
    interns = db.scalars(
        select(Intern).where(Intern.remarks.like(marker))
    ).all()
    intern_ids = [i.id for i in interns]
    if intern_ids:
        for model, label in (
            (AttendanceAdjustment, "adjustments"),
            (ExposureLog, "exposure_logs"),
            (InternEvaluation, "evaluations"),
            (TechnicalAssignment, "technical_assignments"),
            (Timesheet, "timesheets"),
            (LeaveRequest, "leave_requests"),
            (OJTProgressSnapshot, "progress_snapshots"),
            (OJTHourLedger, "ledger_entries"),
            (AttendanceRecord, "attendance_records"),
            (ScheduleAssignment, "schedule_assignments"),
        ):
            wipe(label, db.scalars(
                select(model).where(model.intern_id.in_(intern_ids))
            ).all())
        db.flush()
    wipe("interns", interns)
    db.flush()

    # 3. Org records that point at demo accounts, before the accounts.
    wipe("supervisors", db.scalars(
        select(Supervisor).where(Supervisor.remarks.like(marker))
    ).all())
    wipe("mentors", db.scalars(
        select(Mentor).where(Mentor.remarks.like(marker))
    ).all())
    db.flush()

    # 4. Demo accounts. This is the step whose absence mattered most: these
    #    all share one published password, so leaving them behind is a live
    #    set of working credentials on a system someone believes is clean.
    demo_users = db.scalars(
        select(User).where(User.remarks.like(marker))
    ).all()
    blocked: list[str] = []
    for u in demo_users:
        try:
            with db.begin_nested():
                db.delete(u)
        except IntegrityError:
            # Referenced by a record a real user created. Deactivating is the
            # safe outcome: the account can no longer be used, and the record
            # that needs it keeps its author.
            u.is_active = False
            u.must_change_password = True
            blocked.append(u.username)
        else:
            counts["users"] = counts.get("users", 0) + 1
    db.flush()

    # 5. Finally the scaffolding nothing points at any more.
    wipe("holidays", db.scalars(
        select(Holiday).where(Holiday.remarks.like(marker))
    ).all())
    wipe("rest_days", db.scalars(
        select(RestDay).where(RestDay.remarks.like(marker))
    ).all())
    wipe("teams", db.scalars(
        select(Team).where(Team.remarks.like(marker))
    ).all())
    wipe("schools", db.scalars(
        select(School).where(School.remarks.like(marker))
    ).all())
    wipe("departments", db.scalars(
        select(Department).where(Department.remarks.like(marker))
    ).all())
    db.flush()

    if blocked:
        counts["users_deactivated_not_deleted"] = len(blocked)
        counts["_deactivated"] = blocked  # type: ignore[assignment]
    return counts

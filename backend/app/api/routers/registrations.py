"""Intern self-registration.

This module holds the application's only unauthenticated write endpoint, so
the constraints on it are tighter than elsewhere:

  * A submission creates nothing operational. It is a row in its own table
    with no intern, no user account, no role and no schedule, so an open link
    cannot be used to inject a record into the live system.
  * The applicant chooses none of the privileged fields. Status, intern code,
    team, supervisor and required hours are set by the reviewer on approval -
    the schema simply has nowhere to put them.
  * Responses leak nothing. A submission returns only a reference code, and a
    duplicate address gets the same acknowledgement as a new one, so the
    endpoint cannot be used to test whether someone is already enrolled.
  * Volume is capped per IP address, because the endpoint is reachable without
    a credential.
"""
from __future__ import annotations

import secrets
from datetime import date, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import func, select

from app.api.deps import (
    CurrentUser, DbSession, client_ip, get_rules, require_permissions,
)
from app.core.crypto import blind_index
from app.core.enums import InternStatus, RoleName
from app.core.security import hash_password
from app.core.timeutil import business_date_of, utc_now
from app.models.identity import Role, User
from app.models.intern import Intern
from app.models.org import School
from app.models.registration import InternRegistration, RegistrationStatus
from app.schemas.common import Message, Page
from app.schemas.registration import (
    RegistrationAck, RegistrationApprove, RegistrationApproved,
    RegistrationCreate, RegistrationOut, RegistrationReject,
    RegistrationStatusOut,
)
from app.services import audit_service, intern_service, settings_service

router = APIRouter(prefix="/api/registrations", tags=["Registration"])

# Unambiguous alphabet: no O/0 or I/1, since applicants read these codes off a
# screen and type them back in, or quote them over the phone.
_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def _new_reference_code(db: DbSession) -> str:
    """REG-<year>-<8 random chars>, retried on the vanishingly unlikely clash."""
    year = business_date_of(utc_now()).year
    for _ in range(10):
        body = "".join(secrets.choice(_CODE_ALPHABET) for _ in range(8))
        code = f"REG-{year}-{body}"
        if not db.scalar(
            select(InternRegistration.id).where(
                InternRegistration.reference_code == code
            )
        ):
            return code
    raise HTTPException(
        status.HTTP_503_SERVICE_UNAVAILABLE,
        "Could not allocate a reference code. Please try again.",
    )


# --- Public ----------------------------------------------------------------


@router.get("/open", response_model=dict, summary="Is registration accepting?")
def registration_open(db: DbSession) -> dict:
    """Lets the public page show a closed notice instead of a dead form."""
    rules = settings_service.load_rules(db)
    return {
        "open": rules.bool_("registration_open"),
        "coordinator_email_required": rules.bool_(
            "registration_requires_coordinator_email"
        ),
    }


@router.post(
    "",
    response_model=RegistrationAck,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Submit a registration (public)",
)
def submit(
    payload: RegistrationCreate,
    db: DbSession,
    request: Request,
) -> RegistrationAck:
    rules = settings_service.load_rules(db)

    if not rules.bool_("registration_open"):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Registration is closed at the moment. Please contact the IT "
            "department for the next intake.",
        )

    if rules.bool_("registration_requires_coordinator_email") and not (
        payload.coordinator_email
    ):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "Your school's OJT or IT coordinator email is required. The signed "
            "timesheet has to be sent there when you finish.",
        )

    ip = client_ip(request)
    cap = rules.int_("registration_max_per_ip_per_hour")
    if ip and cap > 0:
        recent = db.scalar(
            select(func.count(InternRegistration.id)).where(
                InternRegistration.submitted_ip == ip,
                InternRegistration.created_at >= utc_now() - timedelta(hours=1),
            )
        )
        if (recent or 0) >= cap:
            # 429 with a plain explanation: a genuine group from one campus
            # network can hit this, and they need to know it is temporary.
            raise HTTPException(
                status.HTTP_429_TOO_MANY_REQUESTS,
                "Several registrations have already been submitted from this "
                "network in the past hour. Please try again later, or ask the "
                "IT department to register you directly.",
            )

    # A named school must actually exist and be open for intake.
    if payload.school_id is not None:
        school = db.get(School, payload.school_id)
        if school is None or school.deleted_at is not None or not school.is_active:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                "That school is not on file. Choose another, or type the name "
                "so the IT department can add it.",
            )

    reg = InternRegistration(
        reference_code=_new_reference_code(db),
        first_name=payload.first_name,
        middle_name=payload.middle_name,
        last_name=payload.last_name,
        email=str(payload.email),
        contact_number=payload.contact_number,
        school_id=payload.school_id,
        school_name_text=payload.school_name_text,
        course=payload.course,
        year_level=payload.year_level,
        coordinator_name=payload.coordinator_name,
        coordinator_email=(
            str(payload.coordinator_email) if payload.coordinator_email else None
        ),
        coordinator_contact=payload.coordinator_contact,
        required_hours=payload.required_hours,
        ojt_start_date=payload.ojt_start_date,
        school_submission_deadline=payload.school_submission_deadline,
        primary_technical_function=payload.primary_technical_function,
        emergency_contact_name=payload.emergency_contact_name,
        emergency_contact_number=payload.emergency_contact_number,
        emergency_contact_relation=payload.emergency_contact_relation,
        applicant_notes=payload.applicant_notes,
        status=RegistrationStatus.PENDING,
        submitted_ip=ip,
        submitted_user_agent=request.headers.get("user-agent"),
    )
    reg.refresh_email_index()
    db.add(reg)
    db.flush()

    # No actor: the submitter is not authenticated. The IP and reference code
    # are what make the entry traceable.
    audit_service.record(
        db,
        action="REGISTER",
        entity_type="intern_registration",
        entity_id=reg.id,
        summary=f"Registration {reg.reference_code} submitted",
        ip_address=ip,
        user_agent=request.headers.get("user-agent"),
    )
    db.commit()

    # Identical whether or not this address is already known, so the endpoint
    # cannot be used to probe who is enrolled.
    return RegistrationAck(
        reference_code=reg.reference_code,
        status=reg.status,
        message=(
            "Your registration has been received and is waiting for review by "
            "the IT department. Keep your reference code: you can use it to "
            "check the status."
        ),
    )


@router.get(
    "/status/{reference_code}",
    response_model=RegistrationStatusOut,
    summary="Check a registration (public)",
)
def check_status(reference_code: str, db: DbSession) -> RegistrationStatusOut:
    """Status by reference code.

    The code is random and 8 characters from a 32-letter alphabet, so it is
    not guessable; it is the only thing that authorises this lookup, which is
    why no personal detail is returned even to a correct code.
    """
    reg = db.scalar(
        select(InternRegistration).where(
            InternRegistration.reference_code == reference_code.strip().upper()
        )
    )
    if reg is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            "No registration found for that reference code.",
        )
    return RegistrationStatusOut(
        reference_code=reg.reference_code,
        status=reg.status,
        submitted_on=reg.created_at.date(),
        reviewed_on=reg.reviewed_at.date() if reg.reviewed_at else None,
        review_notes=reg.review_notes,
    )


# --- Reviewer --------------------------------------------------------------


def _duplicate_intern_id(db: DbSession, reg: InternRegistration) -> int | None:
    """Existing intern with the same address, found via the blind index."""
    if not reg.email_index:
        return None
    return db.scalar(
        select(Intern.id).where(
            Intern.email_index == reg.email_index, Intern.deleted_at.is_(None)
        )
    )


def _out(db: DbSession, reg: InternRegistration) -> RegistrationOut:
    data = RegistrationOut.model_validate(reg)
    data.duplicate_of_intern_id = _duplicate_intern_id(db, reg)
    return data


@router.get("", response_model=Page[RegistrationOut], summary="List registrations")
def list_registrations(
    db: DbSession,
    user: Annotated[CurrentUser, Depends(require_permissions("registration.read"))],
    status_filter: str | None = Query(
        default=RegistrationStatus.PENDING, alias="status",
        description="PENDING | APPROVED | REJECTED | WITHDRAWN, or blank for all",
    ),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
) -> Page[RegistrationOut]:
    stmt = select(InternRegistration)
    if status_filter:
        wanted = status_filter.strip().upper()
        if wanted not in RegistrationStatus.ALL:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                f"Unknown status '{status_filter}'. Valid: "
                f"{', '.join(RegistrationStatus.ALL)}.",
            )
        stmt = stmt.where(InternRegistration.status == wanted)

    total = db.scalar(
        select(func.count()).select_from(stmt.subquery())
    ) or 0
    rows = db.scalars(
        stmt.order_by(InternRegistration.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    return Page[RegistrationOut](
        items=[_out(db, r) for r in rows],
        total=total, page=page, page_size=page_size,
    )


@router.get("/{registration_id}", response_model=RegistrationOut, summary="One")
def get_registration(
    registration_id: int,
    db: DbSession,
    user: Annotated[CurrentUser, Depends(require_permissions("registration.read"))],
) -> RegistrationOut:
    reg = db.get(InternRegistration, registration_id)
    if reg is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Registration not found.")
    return _out(db, reg)


@router.post(
    "/{registration_id}/approve",
    response_model=RegistrationApproved,
    summary="Approve and create the intern",
)
def approve(
    registration_id: int,
    payload: RegistrationApprove,
    db: DbSession,
    request: Request,
    user: Annotated[
        CurrentUser, Depends(require_permissions("registration.review"))
    ],
) -> RegistrationApproved:
    """Creates the intern record, and optionally a sign-in account.

    The registration row is not edited into the intern: it stays as a record
    of what the applicant actually submitted, which is what makes a later
    dispute about dates or required hours answerable.
    """
    reg = db.get(InternRegistration, registration_id)
    if reg is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Registration not found.")
    if reg.status != RegistrationStatus.PENDING:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"This registration was already {reg.status.lower()}"
            + (
                f" and created intern #{reg.created_intern_id}."
                if reg.created_intern_id else "."
            ),
        )

    duplicate = _duplicate_intern_id(db, reg)
    if duplicate is not None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"An intern with this email address already exists (#{duplicate}). "
            f"Reject this submission as a duplicate rather than creating a "
            f"second record, which would split their hours across two ledgers.",
        )

    school_id = payload.school_id if payload.school_id is not None else reg.school_id
    if school_id is None:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"The applicant typed '{reg.school_name_text}' rather than choosing "
            f"a school on file. Add that school first, then approve with its id.",
        )
    school = db.get(School, school_id)
    if school is None or school.deleted_at is not None:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY, "That school does not exist."
        )

    rules = get_rules(db)
    required = (
        payload.required_hours
        or reg.required_hours
        or school.default_required_hours
        or rules.int_("default_required_hours")
    )
    start = payload.ojt_start_date or reg.ojt_start_date or business_date_of(utc_now())

    intern = intern_service.create(
        db,
        {
            "first_name": reg.first_name,
            "middle_name": reg.middle_name,
            "last_name": reg.last_name,
            "email": reg.email,
            "contact_number": reg.contact_number,
            "emergency_contact_name": reg.emergency_contact_name,
            "emergency_contact_number": reg.emergency_contact_number,
            "emergency_contact_relation": reg.emergency_contact_relation,
            "school_id": school_id,
            # Denormalised onto the intern so the timesheet names the person
            # who signed off at the time, even if the school later changes
            # coordinator.
            "school_coordinator": reg.coordinator_name or school.coordinator_name,
            "course": reg.course,
            "year_level": reg.year_level,
            "ojt_start_date": start,
            "school_submission_deadline": (
                payload.school_submission_deadline
                or reg.school_submission_deadline
            ),
            "required_hours": required,
            "department_id": payload.department_id,
            "team_id": payload.team_id,
            "supervisor_id": payload.supervisor_id,
            "mentor_id": payload.mentor_id,
            "primary_technical_function": (
                payload.primary_technical_function
                or reg.primary_technical_function
            ),
            "status": InternStatus.ACTIVE,
            "remarks": f"Created from registration {reg.reference_code}.",
        },
        actor_user_id=user.id,
    )

    username: str | None = None
    temp_password: str | None = None
    if payload.create_user_account:
        username, temp_password = _provision_intern_account(db, intern, reg, user)

    reg.status = RegistrationStatus.APPROVED
    reg.reviewed_by_user_id = user.id
    reg.reviewed_at = utc_now()
    reg.review_notes = payload.review_notes
    reg.created_intern_id = intern.id

    audit_service.record(
        db, action="APPROVE", entity_type="intern_registration", entity_id=reg.id,
        actor=user,
        summary=(
            f"Approved {reg.reference_code}; created intern {intern.intern_code}"
            + (f" and account {username}" if username else "")
        ),
        ip_address=client_ip(request),
    )
    db.commit()

    return RegistrationApproved(
        intern_id=intern.id,
        intern_code=intern.intern_code,
        username=username,
        temporary_password=temp_password,
        message=(
            f"{intern.display_name} is now an active intern "
            f"({intern.intern_code}), enrolled under {school.name}."
            + (
                " Give them the temporary password below; they must change it "
                "at first sign-in. It is shown once and is not recoverable."
                if temp_password else ""
            )
        ),
    )


def _provision_intern_account(
    db: DbSession,
    intern: Intern,
    reg: InternRegistration,
    actor: User,
) -> tuple[str, str]:
    """Create the intern's sign-in account with a one-time password."""
    role = db.scalar(select(Role).where(Role.name == RoleName.INTERN))
    if role is None:
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            "The Intern role is missing. Run `python -m app.cli bootstrap`.",
        )

    base = (
        f"{reg.first_name}.{reg.last_name}".lower()
        .replace(" ", "").replace("'", "").replace("-", "")
    )
    base = "".join(c for c in base if c.isalnum() or c == ".")[:40] or "intern"
    username = base
    suffix = 2
    while db.scalar(select(User.id).where(User.username == username)):
        username = f"{base}{suffix}"
        suffix += 1

    idx = blind_index(reg.email)
    if idx and db.scalar(select(User.id).where(User.email_index == idx)):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"A user account already uses {reg.first_name}'s email address. "
            f"Approve without an account, then link the existing one.",
        )

    # Generated rather than chosen: a reviewer-invented password tends to be a
    # pattern reused across interns. must_change_password forces replacement
    # at first sign-in regardless.
    temp_password = secrets.token_urlsafe(12)
    account = User(
        username=username,
        email=reg.email,
        full_name=intern.display_name,
        password_hash=hash_password(temp_password),
        contact_number=reg.contact_number,
        position_title="OJT Intern",
        department_id=intern.department_id,
        must_change_password=True,
        created_by_id=actor.id,
    )
    account.sync_lookup_indexes()
    account.roles = [role]
    db.add(account)
    db.flush()

    intern.user_id = account.id
    db.flush()
    return username, temp_password


@router.post(
    "/{registration_id}/reject", response_model=Message, summary="Reject"
)
def reject(
    registration_id: int,
    payload: RegistrationReject,
    db: DbSession,
    request: Request,
    user: Annotated[
        CurrentUser, Depends(require_permissions("registration.review"))
    ],
) -> Message:
    reg = db.get(InternRegistration, registration_id)
    if reg is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Registration not found.")
    if reg.status != RegistrationStatus.PENDING:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"This registration was already {reg.status.lower()}.",
        )

    reg.status = RegistrationStatus.REJECTED
    reg.reviewed_by_user_id = user.id
    reg.reviewed_at = utc_now()
    # Visible to the applicant through the public status check, which the
    # reviewer is told in the form.
    reg.review_notes = payload.review_notes

    audit_service.record(
        db, action="REJECT", entity_type="intern_registration", entity_id=reg.id,
        actor=user, summary=f"Rejected {reg.reference_code}",
        ip_address=client_ip(request),
    )
    db.commit()
    return Message(
        detail=(
            f"{reg.reference_code} rejected. The applicant will see your note "
            f"when they check their reference code."
        )
    )

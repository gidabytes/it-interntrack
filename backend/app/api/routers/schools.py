"""Schools and their OJT / IT coordinators.

The coordinator is the school-side contact who signs off OJT hours and
receives the completed timesheet, so their name and address are operational
data rather than a nicety: without them the signed timesheet has nowhere to
go at the end of the placement.
"""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import func, select

from app.api.deps import CurrentUser, DbSession, client_ip, require_permissions
from app.core.enums import InternStatus
from app.models.intern import Intern
from app.models.org import School
from app.schemas.common import Message
from app.schemas.school import SchoolCreate, SchoolOut, SchoolUpdate
from app.services import audit_service

router = APIRouter(prefix="/api/schools", tags=["Schools"])

AUDIT_FIELDS = [
    "name", "short_name", "address", "coordinator_name", "coordinator_email",
    "coordinator_contact", "default_required_hours", "is_active",
]


def _counts(db: DbSession, school_ids: list[int]) -> dict[int, tuple[int, int]]:
    """Intern totals per school, as one grouped query rather than per row."""
    if not school_ids:
        return {}
    rows = db.execute(
        select(
            Intern.school_id,
            func.count(Intern.id),
            func.sum(
                # Counted with a CASE rather than a second query so the active
                # tally comes back in the same pass.
                func.cast(Intern.status == InternStatus.ACTIVE, type_=None)
                if False else
                func.coalesce(
                    func.nullif(Intern.status, InternStatus.ACTIVE), None
                ).is_(None).cast(__import__("sqlalchemy").Integer)
            ),
        )
        .where(Intern.school_id.in_(school_ids), Intern.deleted_at.is_(None))
        .group_by(Intern.school_id)
    ).all()
    return {r[0]: (int(r[1] or 0), int(r[2] or 0)) for r in rows}


def _out(school: School, counts: dict[int, tuple[int, int]]) -> SchoolOut:
    total, active = counts.get(school.id, (0, 0))
    data = SchoolOut.model_validate(school)
    data.intern_count = total
    data.active_intern_count = active
    return data


@router.get("", response_model=list[SchoolOut], summary="List schools")
def list_schools(
    db: DbSession,
    user: Annotated[CurrentUser, Depends(require_permissions("school.read"))],
    q: str | None = Query(default=None, description="Match name or short name"),
    include_inactive: bool = False,
) -> list[SchoolOut]:
    stmt = select(School).where(School.deleted_at.is_(None))
    if not include_inactive:
        stmt = stmt.where(School.is_active.is_(True))
    if q:
        like = f"%{q.strip()}%"
        stmt = stmt.where(School.name.ilike(like) | School.short_name.ilike(like))
    schools = db.scalars(stmt.order_by(School.name)).all()
    counts = _counts(db, [s.id for s in schools])
    return [_out(s, counts) for s in schools]


@router.get(
    "/public",
    response_model=list[dict],
    summary="School names for the public registration form",
)
def public_schools(db: DbSession) -> list[dict]:
    """Unauthenticated: feeds the school picker on the registration page.

    Returns names only. The coordinator's details are withheld because an
    open endpoint that listed staff addresses would be a harvesting target,
    and the applicant has no need for them.
    """
    schools = db.scalars(
        select(School)
        .where(School.deleted_at.is_(None), School.is_active.is_(True))
        .order_by(School.name)
    ).all()
    return [
        {"id": s.id, "name": s.name, "short_name": s.short_name} for s in schools
    ]


@router.get("/{school_id}", response_model=SchoolOut, summary="One school")
def get_school(
    school_id: int,
    db: DbSession,
    user: Annotated[CurrentUser, Depends(require_permissions("school.read"))],
) -> SchoolOut:
    school = db.get(School, school_id)
    if school is None or school.deleted_at is not None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "School not found.")
    return _out(school, _counts(db, [school.id]))


@router.post(
    "", response_model=SchoolOut, status_code=status.HTTP_201_CREATED,
    summary="Add a school",
)
def create_school(
    payload: SchoolCreate,
    db: DbSession,
    request: Request,
    user: Annotated[CurrentUser, Depends(require_permissions("school.manage"))],
) -> SchoolOut:
    existing = db.scalar(
        select(School).where(
            func.lower(School.name) == payload.name.lower(),
            School.deleted_at.is_(None),
        )
    )
    if existing is not None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"'{existing.name}' is already on file. Edit that record instead "
            f"of adding a second one, so interns are not split across two "
            f"entries for the same school.",
        )

    school = School(
        **payload.model_dump(exclude_none=False, exclude={"remarks"}),
        remarks=payload.remarks,
    )
    # EmailStr is not a str subclass that SQLAlchemy should store directly.
    if payload.coordinator_email is not None:
        school.coordinator_email = str(payload.coordinator_email)
    db.add(school)
    db.flush()

    audit_service.record(
        db, action="CREATE", entity_type="school", entity_id=school.id, actor=user,
        summary=f"Added school {school.name}", after=school, fields=AUDIT_FIELDS,
        ip_address=client_ip(request),
    )
    db.commit()
    db.refresh(school)
    return _out(school, {})


@router.patch("/{school_id}", response_model=SchoolOut, summary="Edit a school")
def update_school(
    school_id: int,
    payload: SchoolUpdate,
    db: DbSession,
    request: Request,
    user: Annotated[CurrentUser, Depends(require_permissions("school.manage"))],
) -> SchoolOut:
    school = db.get(School, school_id)
    if school is None or school.deleted_at is not None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "School not found.")

    before = audit_service.snapshot(school, AUDIT_FIELDS)
    changes = payload.model_dump(exclude_unset=True)
    if "coordinator_email" in changes and changes["coordinator_email"] is not None:
        changes["coordinator_email"] = str(changes["coordinator_email"])
    for field, value in changes.items():
        setattr(school, field, value)
    db.flush()

    audit_service.record(
        db, action="UPDATE", entity_type="school", entity_id=school.id, actor=user,
        summary=f"Edited school {school.name}", before=before, after=school,
        fields=AUDIT_FIELDS, ip_address=client_ip(request),
    )
    db.commit()
    db.refresh(school)
    return _out(school, _counts(db, [school.id]))


@router.delete("/{school_id}", response_model=Message, summary="Archive a school")
def archive_school(
    school_id: int,
    db: DbSession,
    request: Request,
    user: Annotated[CurrentUser, Depends(require_permissions("school.manage"))],
) -> Message:
    """Deactivates rather than deletes.

    Interns reference the school, and their completed timesheets name its
    coordinator. Removing the row would orphan that history, so the school is
    hidden from pickers and kept.
    """
    school = db.get(School, school_id)
    if school is None or school.deleted_at is not None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "School not found.")

    active = db.scalar(
        select(func.count(Intern.id)).where(
            Intern.school_id == school_id,
            Intern.status == InternStatus.ACTIVE,
            Intern.deleted_at.is_(None),
        )
    )
    if active:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"{active} active intern(s) are still enrolled under "
            f"{school.name}. Complete or reassign them first.",
        )

    school.is_active = False
    audit_service.record(
        db, action="ARCHIVE", entity_type="school", entity_id=school.id, actor=user,
        summary=f"Deactivated school {school.name}", ip_address=client_ip(request),
    )
    db.commit()
    return Message(detail=f"{school.name} is no longer offered in pickers.")

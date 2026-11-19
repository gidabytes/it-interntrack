"""Intern / OJT profile management."""
from __future__ import annotations

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from app.api.deps import (
    CurrentUser, DbSession, assert_can_view_intern, client_ip, get_intern_or_404,
    own_intern_profile, require_permissions, supervised_intern_ids,
)
from app.core.enums import RoleName
from app.schemas.common import Message, Page
from app.schemas.intern import InternCreate, InternOut, InternSummaryOut, InternUpdate
from app.services import audit_service, intern_service

router = APIRouter(prefix="/api/interns", tags=["Interns"])

AUDIT_FIELDS = [
    "intern_code", "first_name", "middle_name", "last_name", "status",
    "required_hours", "ojt_start_date", "expected_completion_date",
    "school_submission_deadline", "team_id", "supervisor_id", "mentor_id",
    "department_id", "primary_technical_function", "contact_number", "email",
]


@router.get("", response_model=Page[InternOut], summary="List / search interns")
def list_interns(
    db: DbSession,
    user: Annotated[
        object, Depends(require_permissions("intern.read", "intern.read.assigned",
                                            "intern.read.self"))
    ],
    q: str | None = None,
    status_filter: str | None = Query(default=None, alias="status"),
    team_id: int | None = None,
    department_id: int | None = None,
    supervisor_id: int | None = None,
    mentor_id: int | None = None,
    school_id: int | None = None,
    include_archived: bool = False,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
) -> Page[InternOut]:
    # Scope the result set to what this role may see.
    restrict: set[int] | None = None
    if not user.has_role(RoleName.SYSTEM_ADMINISTRATOR, RoleName.IT_MANAGER):
        if user.has_role(RoleName.INTERN):
            own = own_intern_profile(db, user)
            restrict = {own.id} if own else set()
        else:
            restrict = supervised_intern_ids(db, user)

    rows, total = intern_service.search(
        db, query=q, status=status_filter, team_id=team_id,
        department_id=department_id, supervisor_id=supervisor_id,
        mentor_id=mentor_id, school_id=school_id,
        include_archived=include_archived, restrict_to_ids=restrict,
        page=page, page_size=page_size,
    )
    return Page[InternOut](
        items=[InternOut.model_validate(r) for r in rows],
        total=total, page=page, page_size=page_size,
    )


@router.get("/me", response_model=InternOut, summary="Own intern profile")
def my_profile(db: DbSession, user: CurrentUser) -> InternOut:
    intern = own_intern_profile(db, user)
    if intern is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="This account is not linked to an intern profile.",
        )
    return InternOut.model_validate(intern)


@router.get("/{intern_id}", response_model=InternOut)
def get_intern(intern_id: int, db: DbSession, user: CurrentUser) -> InternOut:
    intern = get_intern_or_404(db, intern_id)
    assert_can_view_intern(db, user, intern)
    return InternOut.model_validate(intern)


@router.post(
    "", response_model=InternOut, status_code=status.HTTP_201_CREATED,
    summary="Register an intern",
)
def create_intern(
    payload: InternCreate,
    db: DbSession,
    request: Request,
    user: Annotated[object, Depends(require_permissions("intern.manage"))],
) -> InternOut:
    intern = intern_service.create(
        db, payload.model_dump(), actor_user_id=user.id
    )
    audit_service.record(
        db, action="CREATE", entity_type="intern", entity_id=intern.id, actor=user,
        summary=f"Registered intern {intern.intern_code} ({intern.display_name})",
        after=intern, fields=AUDIT_FIELDS, ip_address=client_ip(request),
    )
    db.commit()
    return InternOut.model_validate(intern)


@router.patch("/{intern_id}", response_model=InternOut)
def update_intern(
    intern_id: int,
    payload: InternUpdate,
    db: DbSession,
    request: Request,
    user: Annotated[object, Depends(require_permissions("intern.manage"))],
) -> InternOut:
    intern = get_intern_or_404(db, intern_id)
    assert_can_view_intern(db, user, intern)

    before = audit_service._snapshot(intern, AUDIT_FIELDS)
    data = payload.model_dump(exclude_unset=True)
    intern_service.update(db, intern, data, actor_user_id=user.id)

    entry = audit_service.record(
        db, action="UPDATE", entity_type="intern", entity_id=intern.id, actor=user,
        summary=f"Updated {', '.join(sorted(data.keys()))}",
        after=intern, fields=AUDIT_FIELDS, ip_address=client_ip(request),
    )
    entry.before_state = before
    db.commit()
    return InternOut.model_validate(intern)


@router.delete("/{intern_id}", response_model=Message, summary="Archive an intern")
def archive_intern(
    intern_id: int,
    db: DbSession,
    request: Request,
    user: Annotated[object, Depends(require_permissions("intern.manage"))],
) -> Message:
    intern = get_intern_or_404(db, intern_id)
    assert_can_view_intern(db, user, intern)
    intern_service.archive(db, intern, actor_user_id=user.id)
    audit_service.record(
        db, action="ARCHIVE", entity_type="intern", entity_id=intern.id, actor=user,
        summary=f"Archived intern {intern.intern_code}",
        ip_address=client_ip(request),
    )
    db.commit()
    return Message(
        detail=f"Intern {intern.intern_code} archived. Historical records are retained."
    )


@router.post("/{intern_id}/complete", response_model=InternOut)
def complete_intern(
    intern_id: int,
    db: DbSession,
    request: Request,
    user: Annotated[object, Depends(require_permissions("intern.manage"))],
    completion_date: date | None = None,
) -> InternOut:
    """Mark an intern as having completed their OJT."""
    from app.services.ojt_engine import get_progress

    intern = get_intern_or_404(db, intern_id)
    assert_can_view_intern(db, user, intern)

    progress = get_progress(db, intern)
    if not progress.is_complete:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"{intern.display_name} has {progress.remaining_minutes / 60:.2f} "
                f"approved hours remaining ({progress.completion_percentage}% complete). "
                f"Post an approved adjustment first if the shortfall is intentional."
            ),
        )
    intern_service.mark_completed(
        db, intern, completion_date=completion_date, actor_user_id=user.id
    )
    audit_service.record(
        db, action="COMPLETE", entity_type="intern", entity_id=intern.id, actor=user,
        summary=(
            f"Marked complete at {progress.completion_percentage}% "
            f"({progress.approved_minutes / 60:.2f}h approved)"
        ),
        ip_address=client_ip(request),
    )
    db.commit()
    return InternOut.model_validate(intern)

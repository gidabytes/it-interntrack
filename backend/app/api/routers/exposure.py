"""Technical Exposure Tracker endpoints."""
from __future__ import annotations

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.api.deps import (
    CurrentUser, DbSession, assert_can_view_intern, client_ip, get_intern_or_404,
    own_intern_profile, require_permissions,
)
from app.core.enums import ExposureArea, ProficiencyLevel
from app.models.technical import ExposureLog, ExposureTarget
from app.services import audit_service, exposure_service
from app.services.exposure_service import ExposureError

router = APIRouter(prefix="/api/exposure", tags=["Technical Exposure"])


class ExposureCreate(BaseModel):
    intern_id: int
    activity_date: date
    exposure_area: str
    activity_title: str = Field(min_length=3, max_length=250)
    minutes_spent: int = Field(ge=0, le=1440)
    description: str | None = Field(default=None, max_length=2000)
    proficiency_level: str = ProficiencyLevel.ASSISTED
    reference_ticket: str | None = Field(default=None, max_length=80)
    project_name: str | None = Field(default=None, max_length=180)
    asset_tag: str | None = Field(default=None, max_length=80)


class VerifyRequest(BaseModel):
    feedback: str | None = Field(default=None, max_length=2000)


class TargetCreate(BaseModel):
    exposure_area: str
    target_minutes: int = Field(ge=0, le=100000)
    intern_id: int | None = None
    team_id: int | None = None
    is_required: bool = True


@router.get("/areas", summary="Exposure taxonomy")
def areas(user: CurrentUser) -> dict:
    """The tracked IT experience areas, with the core set flagged."""
    return {
        "core_areas": [
            {"key": a, "label": exposure_service.AREA_LABELS.get(a, a), "is_core": True}
            for a in exposure_service.CORE_AREAS
        ],
        "all_areas": [
            {
                "key": a.value,
                "label": exposure_service.AREA_LABELS.get(a.value, a.value),
                "is_core": a.value in exposure_service.CORE_AREAS,
            }
            for a in ExposureArea
        ],
        "proficiency_levels": [
            {"key": p.value, "rank": exposure_service.PROFICIENCY_RANK.get(p.value, 0)}
            for p in ProficiencyLevel
        ],
    }


@router.post("/logs", status_code=status.HTTP_201_CREATED,
             summary="Log a technical activity")
def create_log(
    payload: ExposureCreate,
    db: DbSession,
    request: Request,
    user: Annotated[object, Depends(require_permissions("exposure.log"))],
) -> dict:
    intern = get_intern_or_404(db, payload.intern_id)
    assert_can_view_intern(db, user, intern)
    try:
        entry = exposure_service.log_activity(
            db, intern,
            activity_date=payload.activity_date,
            exposure_area=payload.exposure_area,
            activity_title=payload.activity_title,
            minutes_spent=payload.minutes_spent,
            logged_by_id=user.id,
            description=payload.description,
            proficiency_level=payload.proficiency_level,
            reference_ticket=payload.reference_ticket,
            project_name=payload.project_name,
            asset_tag=payload.asset_tag,
        )
    except ExposureError as exc:
        raise HTTPException(
            status_code=400, detail={"code": exc.code, "message": exc.message}
        ) from exc

    audit_service.record(
        db, action="EXPOSURE_LOGGED", entity_type="exposure_log", entity_id=entry.id,
        actor=user,
        summary=(
            f"{intern.intern_code} {payload.exposure_area} "
            f"{payload.minutes_spent}m: {payload.activity_title[:100]}"
        ),
        ip_address=client_ip(request),
    )
    db.commit()
    return {
        "id": entry.id,
        "intern_id": entry.intern_id,
        "activity_date": entry.activity_date.isoformat(),
        "exposure_area": entry.exposure_area,
        "activity_title": entry.activity_title,
        "minutes_spent": entry.minutes_spent,
        "proficiency_level": entry.proficiency_level,
        "is_verified": entry.is_verified,
    }


@router.post("/logs/{log_id}/verify", summary="Verify a logged activity")
def verify_log(
    log_id: int,
    payload: VerifyRequest,
    db: DbSession,
    request: Request,
    user: Annotated[object, Depends(require_permissions("exposure.verify"))],
) -> dict:
    entry = db.get(ExposureLog, log_id)
    if entry is None or entry.is_deleted:
        raise HTTPException(status_code=404, detail="Exposure log not found.")
    intern = get_intern_or_404(db, entry.intern_id)
    assert_can_view_intern(db, user, intern)
    try:
        exposure_service.verify_activity(
            db, entry, verifier_id=user.id, feedback=payload.feedback
        )
    except ExposureError as exc:
        raise HTTPException(
            status_code=403, detail={"code": exc.code, "message": exc.message}
        ) from exc
    audit_service.record(
        db, action="EXPOSURE_VERIFIED", entity_type="exposure_log", entity_id=entry.id,
        actor=user, summary=f"Verified: {entry.activity_title[:120]}",
        ip_address=client_ip(request),
    )
    db.commit()
    return {"id": entry.id, "verified_by": user.full_name,
            "verified_at": entry.verified_at.isoformat()}


@router.get("/me", summary="Own technical exposure")
def my_exposure(
    db: DbSession, user: CurrentUser,
    date_from: date | None = None, date_to: date | None = None,
) -> dict:
    intern = own_intern_profile(db, user)
    if intern is None:
        raise HTTPException(
            status_code=404, detail="This account is not linked to an intern profile."
        )
    return exposure_service.intern_exposure(
        db, intern, date_from=date_from, date_to=date_to
    )


@router.get("/intern/{intern_id}", summary="Exposure matrix for one intern")
def intern_exposure(
    intern_id: int, db: DbSession, user: CurrentUser,
    date_from: date | None = None, date_to: date | None = None,
) -> dict:
    intern = get_intern_or_404(db, intern_id)
    assert_can_view_intern(db, user, intern)
    return exposure_service.intern_exposure(
        db, intern, date_from=date_from, date_to=date_to
    )


@router.get("/matrix", summary="Cross-intern exposure coverage grid")
def matrix(
    db: DbSession,
    user: Annotated[object, Depends(require_permissions("exposure.read"))],
    team_id: int | None = None,
    supervisor_id: int | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
) -> dict:
    """Who has touched what, and which areas the department is not covering."""
    return exposure_service.exposure_matrix(
        db, team_id=team_id, supervisor_id=supervisor_id,
        date_from=date_from, date_to=date_to,
    )


@router.post("/targets", status_code=status.HTTP_201_CREATED,
             summary="Set an exposure target")
def create_target(
    payload: TargetCreate,
    db: DbSession,
    user: Annotated[object, Depends(require_permissions("exposure.verify",
                                                        "settings.manage"))],
) -> dict:
    existing = db.scalar(
        select(ExposureTarget).where(
            ExposureTarget.exposure_area == payload.exposure_area,
            ExposureTarget.intern_id == payload.intern_id,
            ExposureTarget.team_id == payload.team_id,
            ExposureTarget.is_deleted.is_(False),
        )
    )
    if existing:
        existing.target_minutes = payload.target_minutes
        existing.is_required = payload.is_required
        db.commit()
        return {"id": existing.id, "updated": True,
                "target_minutes": existing.target_minutes}
    row = ExposureTarget(**payload.model_dump())
    db.add(row)
    db.commit()
    return {"id": row.id, "updated": False, "target_minutes": row.target_minutes}

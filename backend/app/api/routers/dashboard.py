"""Dashboards, live duty board and shift handover."""
from __future__ import annotations

from datetime import date, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.deps import CurrentUser, DbSession, Rules, require_permissions
from app.core.enums import RoleName
from app.services import dashboard_service, duty_board_service, forecast_service

router = APIRouter(prefix="/api/dashboard", tags=["Dashboards"])


@router.get("", summary="Management dashboard")
def management(
    db: DbSession,
    rules: Rules,
    user: Annotated[object, Depends(require_permissions("dashboard.read"))],
    preset: str = Query(
        default="TODAY",
        description="TODAY | THIS_WEEK | THIS_MONTH | THIS_QUARTER | CUSTOM",
    ),
    date_from: date | None = None,
    date_to: date | None = None,
    team_id: int | None = None,
    supervisor_id: int | None = None,
    department_id: int | None = None,
    today: date | None = None,
) -> dict:
    """KPI cards and analytics, computed from real attendance and ledger data."""
    # A supervisor sees their own interns unless they are admin or manager.
    if not user.has_role(RoleName.SYSTEM_ADMINISTRATOR, RoleName.IT_MANAGER):
        from sqlalchemy import select
        from app.models.org import Supervisor

        own = db.scalar(select(Supervisor).where(Supervisor.user_id == user.id))
        if own:
            supervisor_id = own.id
    try:
        return dashboard_service.management_dashboard(
            db, preset=preset, date_from=date_from, date_to=date_to, today=today,
            team_id=team_id, supervisor_id=supervisor_id,
            department_id=department_id, rules=rules,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/compliance", summary="Management compliance dashboard")
def compliance(
    db: DbSession,
    rules: Rules,
    user: Annotated[object, Depends(require_permissions("dashboard.compliance"))],
    preset: str = "THIS_MONTH",
    date_from: date | None = None,
    date_to: date | None = None,
    department_id: int | None = None,
    today: date | None = None,
) -> dict:
    """Attendance compliance, OJT completion, absences and approval backlog
    across all IT teams."""
    try:
        return dashboard_service.compliance_dashboard(
            db, preset=preset, date_from=date_from, date_to=date_to, today=today,
            department_id=department_id, rules=rules,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/duty-board", summary="Live IT OJT duty board")
def duty_board(
    db: DbSession,
    rules: Rules,
    user: Annotated[object, Depends(require_permissions("dutyboard.read"))],
    team_id: int | None = None,
    supervisor_id: int | None = None,
    department_id: int | None = None,
    include_rest_days: bool = False,
    now: datetime | None = Query(
        default=None, description="Override 'now' (testing / replay)"
    ),
) -> dict:
    """Who is on duty, due in, late, finished or absent right now.

    Correct across overnight shifts: at 02:00 the interns on duty belong to a
    shift whose business date is the previous day.
    """
    return duty_board_service.build_duty_board(
        db, now=now, rules=rules, team_id=team_id, supervisor_id=supervisor_id,
        department_id=department_id, include_rest_days=include_rest_days,
    )


@router.get("/handover", summary="Shift handover monitoring")
def handover(
    db: DbSession,
    rules: Rules,
    user: Annotated[object, Depends(require_permissions("dutyboard.read"))],
    now: datetime | None = None,
) -> dict:
    """Outgoing and incoming interns within the handover window."""
    return duty_board_service.shift_handover_view(db, now=now, rules=rules)


@router.get("/at-risk", summary="Interns at risk of missing their deadline")
def at_risk(
    db: DbSession,
    rules: Rules,
    user: Annotated[object, Depends(require_permissions("dashboard.read"))],
    today: date | None = None,
) -> dict:
    return forecast_service.at_risk_summary(db, rules=rules, today=today)


@router.get("/compliance/by-team", summary="Compliance per IT team")
def by_team(
    db: DbSession,
    rules: Rules,
    user: Annotated[object, Depends(require_permissions("dashboard.read"))],
    date_from: date = Query(...),
    date_to: date = Query(...),
    department_id: int | None = None,
) -> list[dict]:
    return dashboard_service.attendance_by_team(
        db, date_from, date_to, rules=rules, department_id=department_id
    )


@router.get("/approval-backlog", summary="Approval backlog per supervisor")
def backlog(
    db: DbSession,
    rules: Rules,
    user: Annotated[object, Depends(require_permissions("dashboard.read"))],
    supervisor_id: int | None = None,
) -> dict:
    return dashboard_service.approval_backlog(
        db, rules=rules, supervisor_id=supervisor_id
    )

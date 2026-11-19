"""Report generation and export (JSON, Excel, CSV, PDF)."""
from __future__ import annotations

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import Response

from app.api.deps import (
    CurrentUser, DbSession, Rules, assert_can_view_intern, client_ip,
    get_intern_or_404, require_permissions, supervised_intern_ids,
)
from app.core.enums import RoleName
from app.services import audit_service, dashboard_service, report_service

router = APIRouter(prefix="/api/reports", tags=["Reports"])

MEDIA = {
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "csv": "text/csv; charset=utf-8",
    "pdf": "application/pdf",
}


@router.get("", summary="Available reports")
def catalogue(
    user: Annotated[object, Depends(require_permissions("report.read"))],
) -> dict:
    return {
        "reports": [
            {"key": k, "title": title}
            for k, (title, _) in sorted(report_service.REPORTS.items())
        ],
        "formats": ["json", "xlsx", "csv", "pdf"],
        "presets": ["TODAY", "THIS_WEEK", "THIS_MONTH", "THIS_QUARTER", "CUSTOM"],
    }


@router.get("/{report_key}", summary="Generate a report")
def generate(
    report_key: str,
    db: DbSession,
    rules: Rules,
    request: Request,
    user: Annotated[object, Depends(require_permissions("report.read"))],
    fmt: str = Query(default="json", pattern="^(json|xlsx|csv|pdf)$"),
    preset: str = "THIS_MONTH",
    date_from: date | None = None,
    date_to: date | None = None,
    today: date | None = None,
    intern_id: int | None = None,
    team_id: int | None = None,
    supervisor_id: int | None = None,
    include_inactive: bool = False,
):
    """Generate any catalogued report in the requested format.

    Date filters accept a preset or an explicit range. Scope filters (intern,
    team, supervisor) apply where the report supports them.
    """
    if report_key not in report_service.REPORTS:
        raise HTTPException(
            status_code=404,
            detail=(
                f"Unknown report '{report_key}'. "
                f"Available: {', '.join(sorted(report_service.REPORTS))}"
            ),
        )
    if fmt != "json" and not user.has_permission("report.export"):
        raise HTTPException(
            status_code=403,
            detail="Your role may view reports but not export them.",
        )

    try:
        start, end, _ = dashboard_service.resolve_date_range(
            preset, today, date_from, date_to
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    # Scope enforcement: a supervisor or mentor cannot pull the whole site.
    if not user.has_role(RoleName.SYSTEM_ADMINISTRATOR, RoleName.IT_MANAGER):
        allowed = supervised_intern_ids(db, user)
        if intern_id:
            if intern_id not in allowed:
                raise HTTPException(
                    status_code=403, detail="You do not supervise this intern."
                )
        else:
            from sqlalchemy import select
            from app.models.org import Supervisor

            own = db.scalar(select(Supervisor).where(Supervisor.user_id == user.id))
            if own:
                supervisor_id = own.id
            elif not allowed:
                raise HTTPException(
                    status_code=403,
                    detail="You have no assigned interns to report on.",
                )

    if report_key == "individual_timesheet":
        if not intern_id:
            raise HTTPException(
                status_code=400,
                detail="individual_timesheet requires intern_id.",
            )
        assert_can_view_intern(db, user, get_intern_or_404(db, intern_id))

    kwargs: dict = {}
    if intern_id:
        kwargs["intern_id"] = intern_id
    if team_id:
        kwargs["team_id"] = team_id
    if supervisor_id:
        kwargs["supervisor_id"] = supervisor_id
    if include_inactive:
        kwargs["include_inactive"] = True

    try:
        report = report_service.build(db, report_key, start, end, **kwargs)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    audit_service.record(
        db, action="REPORT_GENERATED", entity_type="report", actor=user,
        summary=(
            f"{report.key} as {fmt.upper()} for {start}..{end} "
            f"({len(report.rows)} row(s))"
        ),
        ip_address=client_ip(request),
    )
    db.commit()

    if fmt == "json":
        return report.as_dict()

    filename = f"{report.key}_{start}_{end}.{fmt}"
    if fmt == "csv":
        content = report_service.to_csv(report)
    elif fmt == "xlsx":
        content = report_service.to_xlsx(report, rules)
    else:
        content = report_service.to_pdf(report, rules)

    return Response(
        content=content,
        media_type=MEDIA[fmt],
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )

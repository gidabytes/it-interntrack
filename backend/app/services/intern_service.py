"""Intern master-data operations."""
from __future__ import annotations

from datetime import date

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.crypto import blind_index
from app.core.enums import InternStatus
from app.models.intern import Intern


def next_intern_code(db: Session, *, year: int | None = None) -> str:
    """Generate OJT-<YYYY>-<NNNN>, sequential within the year.

    Derived from the highest existing code for the year rather than a row
    count, so archived interns never cause a collision.
    """
    year = year or date.today().year
    prefix = f"OJT-{year}-"
    codes = db.scalars(
        select(Intern.intern_code).where(Intern.intern_code.like(f"{prefix}%"))
    ).all()
    highest = 0
    for c in codes:
        tail = c.removeprefix(prefix)
        if tail.isdigit():
            highest = max(highest, int(tail))
    return f"{prefix}{highest + 1:04d}"


def find_by_email(db: Session, email: str) -> Intern | None:
    """Look up an intern by email through the blind index.

    The email column is encrypted, so a direct WHERE would never match.
    """
    idx = blind_index(email)
    if not idx:
        return None
    return db.scalar(
        select(Intern).where(
            Intern.email_index == idx, Intern.is_deleted.is_(False)
        )
    )


def search(
    db: Session,
    *,
    query: str | None = None,
    status: str | None = None,
    team_id: int | None = None,
    department_id: int | None = None,
    supervisor_id: int | None = None,
    mentor_id: int | None = None,
    school_id: int | None = None,
    include_archived: bool = False,
    restrict_to_ids: set[int] | None = None,
    page: int = 1,
    page_size: int = 50,
) -> tuple[list[Intern], int]:
    """Filtered, paged intern search.

    Free-text search covers name and intern code only. Email is encrypted and
    therefore not searchable by substring - use ``find_by_email`` for an exact
    match.
    """
    conds = []
    if not include_archived:
        conds.append(Intern.is_deleted.is_(False))
    if status:
        conds.append(Intern.status == status)
    if team_id:
        conds.append(Intern.team_id == team_id)
    if department_id:
        conds.append(Intern.department_id == department_id)
    if supervisor_id:
        conds.append(Intern.supervisor_id == supervisor_id)
    if mentor_id:
        conds.append(Intern.mentor_id == mentor_id)
    if school_id:
        conds.append(Intern.school_id == school_id)
    if restrict_to_ids is not None:
        conds.append(Intern.id.in_(restrict_to_ids or {-1}))
    if query:
        like = f"%{query.strip()}%"
        conds.append(
            (Intern.first_name.ilike(like))
            | (Intern.last_name.ilike(like))
            | (Intern.middle_name.ilike(like))
            | (Intern.intern_code.ilike(like))
        )

    total = db.scalar(select(func.count(Intern.id)).where(*conds)) or 0
    rows = list(
        db.scalars(
            select(Intern)
            .where(*conds)
            .order_by(Intern.last_name, Intern.first_name)
            .offset(max(0, (page - 1) * page_size))
            .limit(page_size)
        ).all()
    )
    return rows, int(total)


def create(db: Session, data: dict, *, actor_user_id: int | None = None) -> Intern:
    intern = Intern(**data)
    intern.intern_code = next_intern_code(db, year=intern.ojt_start_date.year)
    intern.created_by_id = actor_user_id
    intern.sync_lookup_indexes()
    db.add(intern)
    db.flush()
    return intern


def update(
    db: Session, intern: Intern, data: dict, *, actor_user_id: int | None = None
) -> Intern:
    for key, value in data.items():
        if value is not None or key in ("remarks", "email", "contact_number"):
            setattr(intern, key, value)
    intern.updated_by_id = actor_user_id
    # Email may have changed; the blind index must follow or lookups break.
    intern.sync_lookup_indexes()
    db.flush()
    return intern


def archive(
    db: Session, intern: Intern, *, actor_user_id: int | None = None
) -> Intern:
    """Soft delete. History stays traceable, per the spec."""
    from app.core.timeutil import utc_now

    intern.is_deleted = True
    intern.deleted_at = utc_now()
    intern.deleted_by_id = actor_user_id
    if intern.status not in (InternStatus.COMPLETED, InternStatus.WITHDRAWN):
        intern.status = InternStatus.INACTIVE
    db.flush()
    return intern


def mark_completed(
    db: Session, intern: Intern, *, completion_date: date | None = None,
    actor_user_id: int | None = None,
) -> Intern:
    intern.status = InternStatus.COMPLETED
    intern.actual_completion_date = completion_date or date.today()
    intern.updated_by_id = actor_user_id
    db.flush()
    return intern

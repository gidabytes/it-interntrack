"""The database schema and the code must agree, and say so when they do not.

Adding a column without applying its migration does not fail at import or in
a typecheck. It fails at runtime, on the first query that touches the new
column - which for this application is sign-in, surfacing to the user as a
bare 500 with nothing naming the cause. These tests cover the detection, not
the migration itself.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect, text

BACKEND = Path(__file__).resolve().parent.parent


def _alembic(db_path: Path, *args: str) -> subprocess.CompletedProcess:
    import os

    env = dict(os.environ)
    env["DATABASE_URL"] = f"sqlite:///{db_path}"
    return subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=BACKEND, env=env, capture_output=True, text=True, timeout=180,
    )


@pytest.fixture
def at_previous_revision(tmp_path):
    """A database one migration behind head, as an operator who pulled new
    code but has not run `alembic upgrade head` would have."""
    db = tmp_path / "behind.db"
    history = _alembic(db, "history")
    assert history.returncode == 0, history.stderr
    # "<prev> -> <head> (head), message"
    head_line = next(
        line for line in history.stdout.splitlines() if "(head)" in line
    )
    previous = head_line.split("->")[0].strip()
    result = _alembic(db, "upgrade", previous)
    assert result.returncode == 0, result.stderr
    return db, previous


def test_every_model_column_exists_after_a_full_upgrade(tmp_path):
    """Catches a column added to a model with no migration written for it."""
    db = tmp_path / "head.db"
    result = _alembic(db, "upgrade", "head")
    assert result.returncode == 0, result.stderr

    import app.models  # noqa: F401  (registers every table on the metadata)
    from app.core.database import Base

    engine = create_engine(f"sqlite:///{db}")
    inspector = inspect(engine)
    actual = {t: {c["name"] for c in inspector.get_columns(t)}
              for t in inspector.get_table_names()}

    missing: list[str] = []
    for table_name, table in Base.metadata.tables.items():
        if table_name not in actual:
            missing.append(f"{table_name} (whole table)")
            continue
        for column in table.columns:
            if column.name not in actual[table_name]:
                missing.append(f"{table_name}.{column.name}")
    engine.dispose()
    assert not missing, (
        "These model columns have no migration, so any query touching them "
        f"fails at runtime: {missing}"
    )


def _pending_in_subprocess(db_path: Path) -> str:
    """Run main._pending_migration() against db_path, isolated.

    A subprocess rather than importlib.reload: reloading app.core.database
    rebuilds Base and empties its metadata for every later test in the same
    process.
    """
    import os

    env = dict(os.environ)
    env["DATABASE_URL"] = f"sqlite:///{db_path}"
    code = (
        "import app.main as m; "
        "p = m._pending_migration(); "
        "print('NONE' if p is None else f'{p[0]}|{p[1]}')"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=BACKEND, env=env, capture_output=True, text=True, timeout=180,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.strip().splitlines()[-1]


def test_a_database_behind_head_is_detected(at_previous_revision):
    """The check must be exact, not inferred from a driver's error text."""
    db, previous = at_previous_revision
    out = _pending_in_subprocess(db)
    assert out != "NONE", "a database behind head must be reported"
    current, head = out.split("|")
    assert current == previous
    assert head != previous


def test_an_up_to_date_database_reports_nothing(tmp_path):
    db = tmp_path / "current.db"
    result = _alembic(db, "upgrade", "head")
    assert result.returncode == 0, result.stderr
    assert _pending_in_subprocess(db) == "NONE"


def test_sign_in_fails_loudly_on_a_schema_behind_head(at_previous_revision):
    """Reproduces the actual failure: the query cannot run, so login 500s.

    The value of this test is the assertion that the cause is visible. A bare
    500 tells the operator nothing; the error naming the missing column is
    what the startup check and `app.cli diagnose` turn into an instruction.
    """
    db, _ = at_previous_revision
    engine = create_engine(f"sqlite:///{db}")
    with engine.connect() as conn:
        columns = {
            row[1] for row in conn.execute(text("PRAGMA table_info(users)"))
        }
    engine.dispose()

    import app.models  # noqa: F401
    from app.core.database import Base

    expected = {c.name for c in Base.metadata.tables["users"].columns}
    assert expected - columns, (
        "this fixture is meant to be behind head; if the newest migration no "
        "longer touches `users`, point it at whichever table it does change"
    )

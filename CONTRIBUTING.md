# Contributing

## Setting up

```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
python -m app.cli generate-key              # -> FERNET_KEYS
python -m app.cli generate-blind-index-key  # -> BLIND_INDEX_KEY
openssl rand -hex 32                        # -> SECRET_KEY

alembic upgrade head
python -m app.cli bootstrap
python -m app.cli seed-demo
uvicorn app.main:app --reload
```

## Running the tests

```bash
cd backend
PYTHONPATH=. pytest -q
PYTHONPATH=. pytest -m mandatory -v      # the 20 specification scenarios
PYTHONPATH=. pytest -m shift_matrix -v   # all 17 templates
PYTHONPATH=. pytest -m security -v       # auth, RBAC, encryption
```

All tests must pass before a pull request is merged. CI runs them on Python
3.11, 3.12 and 3.13, and separately checks that the migrations apply cleanly and
have not drifted from the models.

## Where things belong

| Layer | Rule |
|---|---|
| `app/services/attendance_engine.py` | **Pure computation. No database access, no I/O.** This is what makes the mandated scenarios testable without a database and recomputation reproducible. Keep it that way. |
| `app/services/*_service.py` | Business operations. Own the transaction boundary via the caller's session; never commit, let the router do that. |
| `app/api/routers/` | HTTP concerns only: validation, authorisation, serialisation, audit. No business logic. |
| `app/models/` | Schema and derived properties. No queries. |

## Rules that are not negotiable

These encode requirements that cost real hours when broken. A change that
violates one will be rejected.

1. **Business date is the shift's scheduled start date.** An overnight session
   is one record. Never split at midnight.
2. **Never mutate a ledger entry.** Corrections reverse and re-post. Totals are
   always derived from the ledger, never cached in a column.
3. **Never recompute history from a live template.** Attendance records carry
   their own shift snapshot. Use it.
4. **Keep `actual` and `creditable` separate.** Caps and clipping affect credit
   only; rendered time stays on record.
5. **Never mark an intern absent before the shift ends and grace elapses.**
6. **An intern never approves their own anything** — attendance, correction,
   hour adjustment, timesheet, exposure log. Enforce in the service layer *and*
   the router.
7. **Every computation rule is configurable.** Add it to
   `settings_service.DEFAULT_SETTINGS` rather than hard-coding a number.
8. **Never log decrypted PII.** The audit trail records that an encrypted field
   changed, not its value.

## Adding a computation rule

1. Add it to `DEFAULT_SETTINGS` in `app/services/settings_service.py`, with a
   description that explains *why* the default is what it is.
2. Read it through `rules.bool_()` / `int_()` / `float_()` / `str_()`.
3. Add a test that exercises both settings of it.

## Adding a report

1. Write a builder in `app/services/report_service.py` returning a `Report`.
2. Register it in `REPORTS`.
3. It gets JSON, Excel, CSV and PDF for free.

## Database changes

```bash
cd backend
alembic revision --autogenerate -m "describe the change"
# Read the generated file. Autogenerate misses constraint renames and data
# migrations, and will happily drop a column you meant to rename.
alembic upgrade head
alembic downgrade -1 && alembic upgrade head   # check it reverses
```

Custom column types (`UTCDateTime`, `EncryptedString`) are rendered by
fully-qualified name; the script template imports the modules that define them.

## Commit messages

Describe the behaviour change and why, not the files touched.

```
Bind overnight attendance to the scheduled start date

A 22:00-07:00 session was producing two records, one per calendar
day, which halved the credited hours. Attendance now binds to the
matched shift's start date.
```

## Timezone

All business logic runs in `Asia/Manila` by default. Timestamps are stored in
UTC and always timezone-aware. Use the helpers in `app/core/timeutil.py`; never
call `datetime.now()` directly, and never build a naive datetime.

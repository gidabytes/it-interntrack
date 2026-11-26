# IT InternTrack

**OJT & Intern Attendance Management System for a 24/7 IT operation.**

Centralised attendance monitoring, automated duty-hour computation, shift
scheduling, OJT progress tracking and management dashboards — built for an IT
department where interns work rotating and overnight shifts rather than a
Monday-to-Friday day shift.

[![tests](https://img.shields.io/badge/tests-162%20passing-brightgreen)](#testing)
[![postgres](https://img.shields.io/badge/verified%20on-PostgreSQL%2016-blue)](docs/SUPABASE.md)
[![license](https://img.shields.io/badge/license-MIT-blue)](LICENSE)
[![python](https://img.shields.io/badge/python-3.11%2B-blue)](#requirements)

---

## Why this exists

Generic timekeeping systems get OJT wrong in three specific ways, and each one
costs an intern real hours:

1. **They split overnight shifts at midnight.** A 22:00–07:00 session becomes
   two half-day records and the intern loses credit. Here, attendance is bound
   to the shift's *scheduled start date*, so that session is one record dated to
   the day it began.
2. **They credit elapsed time instead of worked time.** A nine-hour shift with
   an unpaid lunch is eight creditable hours, not nine.
3. **They keep a running `total_hours` column.** The first out-of-band
   correction makes it wrong, silently and permanently. Here, hours live in an
   append-only ledger and totals are always derived from it.

---

## Core design decisions

| Decision | Why |
|---|---|
| **Business date = scheduled shift start** | An overnight session is one record. A 00:30 time-in binds to the previous day's 22:00 shift as *late*, not to a new day. |
| **Append-only hour ledger** | Corrections post a reversal plus a replacement; nothing is ever edited. Any past figure can be reconstructed and explained. |
| **Per-record shift snapshot** | Each attendance row stores the shift configuration that applied when it was created, so editing a template never rewrites history. |
| **`actual` and `creditable` kept separate** | An intern who renders 10 hours on a day capped at 8 keeps the 10 on record. Caps affect credit, never the evidence. |
| **Derived `is_overnight`** | Computed from the times, never stored, so it cannot drift out of step with them. |
| **Fernet field encryption + blind indexes** | Intern PII is ciphertext at rest; keyed HMAC columns keep lookups working. |
| **Nothing marked absent early** | ABSENT is only set once the shift has ended *and* the grace period has elapsed. Until then the day is `SCHEDULED`. |

---

## Features

### Attendance engine
- 17 preconfigured hourly shift templates (`SHIFT-01` 07:00–16:00 → `SHIFT-17` 23:00–08:00), nine hours each, plus unlimited custom shifts
- Overnight shifts, rotating weekly schedules, daily overrides, temporary changes, per-intern rest days, holidays
- Digital timekeeping (intern self-service) and supervisor manual entry
- Duplicate time-in prevention, overlapping-session prevention, time-out validation, missing-time-out detection
- Multiple breaks per session, paid or unpaid, clipped to the session
- Late, undertime and overtime computation with per-shift grace periods and overtime ceilings
- Supervisor approval workflow with a full audit trail

### OJT hours
- Append-only ledger; totals derived as `SUM(approved AND NOT reversed)`
- `Remaining = Required − Approved`, `Completion = Approved / Required × 100`, capped at 100%
- Configurable daily credit ceiling, with the uncapped actual preserved
- Historical progress snapshots

### The five operational capabilities
1. **Live duty board** — on duty, on break, late with no time-in, missing time-out, absent, due in, upcoming. Correct at 02:00, when on-duty interns belong to yesterday's business date.
2. **Signable digital timesheets** — daily and monthly, frozen at generation, signed with a SHA-256 over the content. Signing locks the underlying records; later corrections create a new revision and leave the signed one intact and verifiable.
3. **Completion forecast** — projects from both recent pace and the assigned roster, reports the later, and flags `AT_RISK` / `WILL_MISS_DEADLINE` against each intern's **school submission deadline**, distinguishing "needs more shifts" from "needs better attendance".
4. **Technical exposure tracker** — logs actual hands-on experience (desktop troubleshooting, AD administration, networking, hardware deployment, patching, documentation, projects) with proficiency levels, a per-intern coverage matrix and department-level gap reporting.
5. **Management compliance dashboard** — attendance compliance and OJT completion per team, approval backlog with aging per supervisor, excessive-absence detection, and a prioritised alert feed.

### Web interface
React + TypeScript + Vite + Tailwind, dark and light themes, built against the
palette in the specification:
- Management dashboard with KPI cards and Recharts analytics
- Live duty board that stays correct across midnight, auto-refreshing
- Compliance dashboard with alerts, per-team bars and supervisor backlog
- Intern list and detail with progress, milestones and deadline forecast
- Attendance register with inline approve and approve-with-overtime
- Month calendar of the resolved schedule
- Timesheets: submit, sign, verify
- Technical exposure matrix with gap shading
- Reports with in-page preview and Excel / CSV / PDF download
- Admin settings with the live deployment check
- Intern self-service: time clock, progress, schedule, own attendance

### Reporting
15 reports, each exportable as **Excel**, **CSV** or **PDF** with print-friendly
layouts and signature blocks: daily attendance, weekly and monthly summaries,
individual timesheet, hours rendered, remaining hours, absenteeism, tardiness,
undertime, overtime, completion monitoring, schedule compliance, supervisor
monitoring, attendance exceptions, technical exposure.

### Security
- JWT authentication, bcrypt password hashing, account lockout after repeated failures
- Role-based access control: 46 permissions across 5 roles
- An intern can never approve their own attendance, sign their own timesheet, verify their own exposure log, or set the timestamp on their own punch
- Fernet encryption at rest for intern PII, with `MultiFernet` key rotation
- Immutable audit trail that records *that* an encrypted field changed, never its plaintext
- All business dates and times in **Asia/Manila**

---

## Requirements

- Python 3.11 or newer
- Node 18 or newer (for the web interface)
- SQLite (bundled) for development or a single instance; **PostgreSQL recommended for production**
- No cloud dependency — designed to run on an internal IT network.
  Supabase or any PostgreSQL works by changing one connection string; see
  [docs/SUPABASE.md](docs/SUPABASE.md)

---

## Quick start

New to the project? **[RUNNING.md](RUNNING.md)** is a step-by-step guide from a
clean machine to a working application, including troubleshooting. On Windows,
use **[INSTALL-WINDOWS.md](INSTALL-WINDOWS.md)** instead — it covers the
PowerShell execution policy, the Microsoft Store Python stub, and Task
Scheduler, and ships setup scripts under `scripts\windows\`. The short version
follows.

```bash
git clone https://github.com/gidabytes/it-interntrack.git
cd it-interntrack/backend

python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt

# Configuration and secrets
cp .env.example .env
python -m app.cli generate-key              # -> FERNET_KEYS in .env
python -m app.cli generate-blind-index-key  # -> BLIND_INDEX_KEY in .env
openssl rand -hex 32                        # -> SECRET_KEY in .env

# Schema, settings, the 17 shift templates and an admin account
alembic upgrade head
python -m app.cli bootstrap --password 'ChangeThisOnFirstSignIn!'

# Optional: realistic demo data, every row marked "DEMO DATA"
python -m app.cli seed-demo

uvicorn app.main:app --reload
```

Then start the web interface in a second terminal:

```bash
cd ../frontend
npm install
npm run dev
```

Open **http://localhost:5174** for the application, or
**http://localhost:8000/api/docs** for the interactive API.

Signing in with demo data: `itmanager`, `rsantos` (supervisor), `jvaldez`
(mentor) or `ana.cruz` (intern) — password `InternTrack#2026` — plus the
`admin` account you set during bootstrap. Each role sees a different
navigation set.

> **Set `FERNET_KEYS` before entering real data.** Without it the application
> refuses to start (unless `DEBUG=true`), because intern PII would otherwise be
> written in plaintext. Back the keys up **separately from the database** — lose
> them and the encrypted columns are unrecoverable.

Check readiness at any time:

```bash
python -m app.cli status
curl -H "Authorization: Bearer <token>" localhost:8000/api/admin/system-status
```

---

## Project layout

```
frontend/            React + TypeScript + Vite + Tailwind
  src/api/           typed client and response shapes
  src/components/    shell and shared UI
  src/hooks/         auth session, theme
  src/lib/           Asia/Manila formatting, status colour mapping
  src/pages/         one file per screen

backend/
  app/
    core/            config, database, Fernet crypto, timezone arithmetic,
                     security, RBAC permission catalogue, enums
    models/          29 SQLAlchemy models
    schemas/          Pydantic request/response models
    services/        attendance_engine   pure computation, no I/O
                     attendance_service  punches, manual entry, finalisation
                     schedule_resolver   precedence + business-date binding
                     ojt_engine          append-only hour ledger
                     forecast_service    completion vs school deadline
                     duty_board_service  live board + shift handover
                     timesheet_service   generate, sign, verify
                     exposure_service    technical exposure tracker
                     dashboard_service   management + compliance dashboards
                     report_service      15 reports, xlsx/csv/pdf renderers
                     settings_service    36 configurable computation rules
                     audit_service       immutable trail
    api/routers/     12 routers, 89 endpoints
    cli.py           administration commands
    seed.py          demo data
    main.py          application entrypoint
  alembic/           migrations
  tests/             162 tests
```

---

## The standard shift matrix

All 17 templates are nine hours elapsed with a one-hour unpaid break, so each
credits **eight** hours.

| Code | Schedule | Overnight | Code | Schedule | Overnight |
|---|---|---|---|---|---|
| SHIFT-01 | 07:00–16:00 | No | SHIFT-10 | 16:00–01:00 | Yes |
| SHIFT-02 | 08:00–17:00 | No | SHIFT-11 | 17:00–02:00 | Yes |
| SHIFT-03 | 09:00–18:00 | No | SHIFT-12 | 18:00–03:00 | Yes |
| SHIFT-04 | 10:00–19:00 | No | SHIFT-13 | 19:00–04:00 | Yes |
| SHIFT-05 | 11:00–20:00 | No | SHIFT-14 | 20:00–05:00 | Yes |
| SHIFT-06 | 12:00–21:00 | No | SHIFT-15 | 21:00–06:00 | Yes |
| SHIFT-07 | 13:00–22:00 | No | SHIFT-16 | 22:00–07:00 | Yes |
| SHIFT-08 | 14:00–23:00 | No | SHIFT-17 | 23:00–08:00 | Yes |
| SHIFT-09 | 15:00–00:00 | Yes | | | |

`SHIFT-09` is the boundary case: an end time of exactly `00:00` is midnight of
the following day and is treated as overnight, not as a zero-length shift.

---

## Worked examples

**A nine-hour shift credits eight hours**

```
Scheduled   07:00 – 16:00      elapsed 540 min
Unpaid lunch                           60 min
Expected working time                 480 min   <- credited, not 540
```

**An overnight session is one record**

```
Scheduled   22:00 – 07:00  (business date 20 Nov)
Time in     20 Nov 21:55
Time out    21 Nov 07:05

business_date        2026-11-20     one record, not two
actual_elapsed       550 min
actual_net           490 min        preserved as rendered
creditable           480 min        clipped to the scheduled 8h
overtime detected     10 min        unapproved, so not credited
```

**Remaining hours**

```
Required  486 h
Approved  210 h
Remaining 276 h
Completion 43.21 %
```

---

## Configuration

36 computation rules are database-backed and editable without a deploy
(`GET/PUT /api/admin/settings`). Among them:

| Setting | Default | Effect |
|---|---|---|
| `max_creditable_minutes_per_day` | 600 | Daily credit ceiling |
| `credit_requires_approval` | true | Only approved attendance counts |
| `cap_completion_at_100` | true | Approved never exceeds required |
| `count_unapproved_overtime` | false | Unauthorised overtime is not credited |
| `approved_leave_counts_as_hours` | false | Leave credits nothing unless enabled |
| `auto_deduct_unpaid_break` | true | Deduct the unpaid break when none is logged |
| `grace_forgives_undertime` | true | An arrival inside grace is not an exception |
| `absence_finalize_grace_minutes` | 60 | Delay before a no-show becomes ABSENT |
| `min_rest_hours_between_shifts` | 8 | Rest-interval conflict check |
| `forecast_lookback_weeks` | 4 | Window for the pace projection |

---

## Testing

```bash
cd backend
PYTHONPATH=. pytest -q                      # 162 tests
PYTHONPATH=. pytest -m mandatory -v         # the 20 required scenarios
PYTHONPATH=. pytest -m shift_matrix -v      # all 17 templates
PYTHONPATH=. pytest -m security -v          # auth, RBAC, encryption
```

The suite runs against **both SQLite and PostgreSQL** — set `TEST_DATABASE_URL`
to exercise Postgres — because the models are meant to be dialect-neutral and
that is how it stays true.

Coverage includes every scenario the specification mandates — regular,
afternoon and overnight shifts; late arrival and early time-out; unpaid and
multiple breaks; missing time-out; duplicate time-in; rest days; approved and
unapproved leave; schedule modification; attendance correction; overtime
approval; 100% completion; remaining-hour arithmetic; multiple schedules in one
week; month-end and year-end rollover; overlapping sessions — plus
double-counting prevention, encryption-at-rest verified by inspecting the raw
database file, key rotation, signature tampering, and self-approval prevention.

---

## Deployment

See **[docs/DEPLOYMENT.md](docs/DEPLOYMENT.md)** for PostgreSQL setup, the nginx
reverse proxy, systemd units, automated backups (including the separate
encryption-key backup), and the pre-production checklist.

See **[docs/ADMIN_GUIDE.md](docs/ADMIN_GUIDE.md)** and
**[docs/USER_GUIDE.md](docs/USER_GUIDE.md)** for day-to-day operation, and
**[docs/STATUS.md](docs/STATUS.md)** for what is built, what is not, and the
known limitations.

---

## Roadmap

Backend, engine, API, frontend and test suite are complete. The main gaps are
schedule editing and intern registration from the UI (both already have tested
endpoints), notification delivery, and file uploads. See
[docs/STATUS.md](docs/STATUS.md) for the full list.

---

## License

MIT — see [LICENSE](LICENSE).

## Schools, registration and sign-in activity

**Schools and the OJT / IT coordinator.** Each partner school carries the
coordinator who signs off hours and receives the completed timesheet. Those
details appear on the intern record, and a missing coordinator email is
flagged rather than left blank — the signed timesheet has nowhere to go
without it. The coordinator named on an intern at enrolment takes precedence
over the school's current one, so a school changing coordinator mid-year does
not rewrite who signed an existing placement.

**Intern self-registration** at `/register`, reachable without an account.
A submission creates nothing operational: no intern, no account, no schedule,
only a row awaiting review. The applicant gets a random reference code to
check their own status. Supervisors review under **Registrations**, where a
duplicate email address and a school that is not yet on file are both flagged
before approval. Approving creates the intern and, optionally, a sign-in
account with a one-time password. The submission is kept as filed, so a later
dispute about dates or hours is answerable.

Close the link between intakes with the `registration_open` setting; the rate
limit per address is `registration_max_per_ip_per_hour`.

**Login Activity** reads the audit trail and shows successful sign-ins,
failed attempts, lockouts and password changes on one timeline, with the IP
address and client for each. Addresses that produced only failures are called
out. Invalid attempts are logged individually, which PCI-DSS 10.2.4 requires.


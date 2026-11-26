# Administrator guide

For the System Administrator and IT Manager. Assumes the system is deployed and
you can sign in.

---

## Roles

| Role | Scope |
|---|---|
| **System Administrator** | Everything: users, roles, settings, shift templates, all reports, the audit trail |
| **IT Manager** | All interns read-only, both dashboards, approvals, all reports, audit trail. No user or settings management |
| **IT Supervisor** | Their assigned interns: schedules, attendance, approvals, timesheet signing, evaluations, reports |
| **IT Mentor** | Their assigned interns read-only, plus exposure logging, verification and evaluations |
| **OJT / Intern** | Their own schedule, punches, attendance, progress, timesheet submission, leave and correction requests |

Three guards hold regardless of role, and are enforced in both the service and
API layers:

- An intern can never approve their own attendance or OJT hour adjustment.
- An intern can never sign their own timesheet, and the intern role cannot sign
  anyone's.
- Whoever submits a correction request cannot be the one who approves it.

---

## First-run setup

1. **Change the administrator password.** `POST /api/auth/change-password`.
2. **Create the real organisation.** Departments, then teams. Give each team its
   primary technical function so the dashboards group correctly.
3. **Create supervisor and mentor accounts.** `POST /api/admin/users` with
   `create_org_record: true`, which also creates the matching Supervisor or
   Mentor record so assignment screens work immediately.
4. **Load the holidays** for the year — `POST /api/schedules/holidays`. Set
   `is_non_working: false` for a holiday the 24/7 floor still staffs.
5. **Review the shift templates.** All 17 standard ones are created on first
   run. Adjust break minutes, grace and overtime ceilings to your actual policy
   *before* interns start, then leave them alone (see below).
6. **Review the computation rules** — `GET /api/admin/settings`. The defaults
   match the specification; confirm they match your department.
7. **Register the schools,** with each school's required hours as the default
   for its interns.

---

## Registering an intern

`POST /api/interns`. The intern code is generated as `OJT-<year>-<NNNN>`.

Two date fields matter and are different:

- **`expected_completion_date`** — your plan for when they will finish.
- **`school_submission_deadline`** — the hard date the school needs the signed
  timesheet by.

**Set the school deadline.** It is what drives the risk flags on the forecast
and the compliance dashboard. Without it an intern shows `NO_DATA` and nobody
is warned when they are about to miss it.

Then: assign a schedule, and create a login account (role `INTERN`) linked via
`user_id` if they are to use digital timekeeping.

---

## Assigning schedules

Four assignment types, highest precedence first:

| Type | Use |
|---|---|
| `DAILY_OVERRIDE` | One specific date — pulling someone in, or a one-off change |
| `TEMPORARY` | A bounded date range replacing the normal pattern |
| `ROTATING` | A repeating multi-week cycle (`rotation_cycle_weeks`, `rotation_week_index`) |
| `WEEKLY_RECURRING` | The normal weekday pattern |

**Validate before saving.** `POST /api/schedules/validate` runs the same checks
without writing, and the UI should call it as the form is filled.

Errors block. Warnings — insufficient rest, mainly — can be overridden by
supplying `conflict_override_reason`, which is recorded as an authorised
exception in the audit trail. Use it deliberately; it exists so a genuine
operational need is not blocked, not to silence the check.

For many interns at once: `POST /api/schedules/assignments/bulk`. Each intern is
validated separately and anything skipped is reported, so one conflict does not
silently drop the batch.

Rest days are per intern. Nothing assumes Monday to Friday.

---

## Editing a shift template

**Editing a template does not change past attendance.** Every record stores a
snapshot of the configuration that applied when it was created, so an edit
affects only future sessions and any record you deliberately recompute.

That is the intended behaviour, and it means you cannot "fix" historical
figures by editing a template. To change a past record, use a correction
request.

Prefer deactivating a template over deleting it — `DELETE /api/shifts/{id}`
deactivates — so historical records keep a valid reference.

---

## Daily operation

### Approve attendance

Unapproved attendance does **not** count toward approved OJT hours. A supervisor
who is behind is quietly holding back their interns' completion, which is why
the compliance dashboard tracks backlog aging per supervisor.

- `GET /api/attendance?approval_status=PENDING`
- `POST /api/attendance/{id}/approve` — set `approve_overtime: true` to
  authorise overtime in the same action
- `POST /api/attendance/bulk-approve` by intern and date range

### Finalise the day

`POST /api/attendance/finalize-day?business_date=YYYY-MM-DD`, or the
`finalize-day` CLI command on a schedule.

It marks absences only where the shift has ended *and* the grace period has
elapsed, and flags still-open sessions as `INCOMPLETE_TIME_RECORD`. Running it
early never marks a working intern absent.

### Watch the duty board

`GET /api/dashboard/duty-board`. The two buckets to act on are
`LATE_NO_TIME_IN` and `MISSING_TIME_OUT`; `needs_attention` sums them with
absences.

The board is correct across midnight: at 02:00 the interns on duty are working a
shift whose business date is the previous day, and the board shows it that way.

---

## Leave

Approved leave does **not** credit OJT hours unless you say so. On approval:

- `is_creditable: false` (default) — the day shows `ON_LEAVE`, credits nothing
- `is_creditable: true` — credits the scheduled hours, or
  `credited_minutes_per_day` if you set a different figure

The global setting `approved_leave_counts_as_hours` changes the default for all
leave. Leave it off unless your department has a policy that says otherwise;
schools generally expect rendered hours.

---

## Timesheets

1. Generate — `POST /api/timesheets/generate` (monthly or daily)
2. The intern submits, or a supervisor submits on their behalf
3. A supervisor signs — `POST /api/timesheets/{id}/sign`

Signing requires every punched record in the period to be approved. That is
deliberate: a signature should not certify figures still in dispute.

**Signing freezes the document and locks the underlying attendance records.** A
later correction produces revision 2; revision 1 stays signed, intact and
verifiable. `GET /api/timesheets/{id}/verify` confirms a signed timesheet has
not been altered since signing — use it if anyone questions a document.

---

## Corrections

An intern files a correction; a supervisor approves it. On approval the
attendance record is recomputed and the hour ledger is **reversed and
re-posted**, never edited, so no hours are double counted and the original
figure stays visible.

Correction types: `TIME_IN`, `TIME_OUT`, `BREAK`, `STATUS`, `OVERTIME`, and
`HOURS_CREDIT` for a direct signed minute adjustment.

Approving a correction against a record locked by a signed timesheet lifts the
lock for that change and records it. Regenerate the timesheet afterwards to
produce a new revision.

---

## Reading the OJT ledger

`GET /api/ojt/ledger/{intern_id}` is the audit trail behind an intern's hours.
Each entry shows `counts_toward_total`. A corrected day looks like this:

```
#12  ATTENDANCE  +480m  counts=false   Attendance 2026-11-02 (PRESENT)
#45  REVERSAL    -480m  counts=false   Reversal of ledger #12: recomputed
#46  ATTENDANCE  +240m  counts=true    Attendance 2026-11-02 (HALF_DAY)
```

Only `#46` counts. Both halves of the reversed pair are excluded — dropping the
original already removes its minutes, so also summing the `-480` would subtract
them twice. The reversal row is there to record *when* and *why*.

If a total ever looks wrong, `POST /api/ojt/rebuild-ledger/{intern_id}` re-syncs
the ledger from attendance. It is idempotent, so running it on a healthy ledger
changes nothing.

---

## Settings

`GET /api/admin/settings`, `PUT /api/admin/settings/{key}`. 36 rules across
seven categories. The ones most likely to need changing:

| Setting | Default | Note |
|---|---|---|
| `max_creditable_minutes_per_day` | 600 | Ceiling per business date |
| `default_grace_period_minutes` | 15 | Fallback when a template sets none |
| `absence_finalize_grace_minutes` | 60 | Delay before a no-show is ABSENT |
| `approved_leave_counts_as_hours` | false | Leave credit policy |
| `min_rest_hours_between_shifts` | 8 | Rest-interval check |
| `excessive_absence_threshold` | 3 | Absences before an alert |
| `approval_backlog_warning_days` | 3 | Age at which approvals count as backlog |
| `attendance_compliance_target` | 95.0 | Dashboard target |
| `forecast_lookback_weeks` | 4 | Pace window |

Changing a rule affects future computations and anything you recompute. It does
not rewrite history, because records carry their own shift snapshot.

---

## Audit trail

`GET /api/admin/audit`, filterable by entity, actor, action and date.

Insert-only. Encrypted values appear as `[ENCRYPTED]` and password hashes as
`[REDACTED]` — the trail records *that* a contact number changed, never what it
changed to, so the audit log never becomes a plaintext copy of the data the
encryption protects.

---

## Interpreting the forecast

Two projections run: recent approved pace, and the hours the assigned roster can
actually deliver. The later date is reported.

| Risk | Meaning | Action |
|---|---|---|
| `ON_TRACK` | Projected comfortably before the deadline | None |
| `AT_RISK` | Either little margin, or the roster covers it but attendance does not | Attendance follow-up |
| `WILL_MISS_DEADLINE` | The roster itself is short by the deadline | Add shifts, or renegotiate the deadline |
| `NO_DATA` | No deadline recorded, or no pace and no upcoming shifts | Set the school deadline |

The distinction matters. `AT_RISK` with `hours_short: 0` means the schedule can
still get them there — the problem is attendance.
`WILL_MISS_DEADLINE` with a positive `hours_short` means no amount of perfect
attendance is enough on the current roster; `recommended_extra_hours_per_week`
tells you how much to add. If that number is implausible, the honest answer is
to talk to the school about the deadline.

---

## When someone cannot sign in

```bash
cd backend && python -m app.cli diagnose
```

Lists every account and its roles, flags lockouts with the time remaining,
checks a password against the stored hashes, and confirms `FERNET_KEYS` can
still read the encrypted fields. The four usual causes — wrong account, no demo
data, lockout, key mismatch — each produce a different and specific message.

Resetting a password as an administrator:
`POST /api/admin/users/{id}/reset-password`. The user is then forced to change
it at next sign-in.

## Demo data

`python -m app.cli seed-demo` creates 12 interns across 5 teams with six weeks
of history, exposure logs, leave requests and evaluations.

Every row is marked `DEMO DATA`.

## Removing the demo data

`seed-demo` loads 12 interns, 3 schools, 5 teams, six weeks of attendance and
**17 sign-in accounts that all share one published password**. Clear it before
real records go in:

```bash
cd backend
python -m app.cli purge-demo
```

On Windows:

```powershell
cd C:\it-interntrack\backend
.\.venv\Scripts\python.exe -m app.cli purge-demo
```

It reports what it removed:

```
Removed: {'registrations': 3, 'exposure_logs': 86, 'evaluations': 6,
          'leave_requests': 4, 'ledger_entries': 219, 'attendance_records': 244,
          'schedule_assignments': 60, 'interns': 12, 'supervisors': 2,
          'mentors': 2, 'users': 17, 'teams': 5, 'schools': 3,
          'departments': 1}
```

**What it removes:** every row tagged `DEMO DATA` — interns and all their
attendance, hours, exposure logs, evaluations, timesheets, leave and
schedules; the demo schools, teams, departments, supervisors and mentors; the
pending demo registrations; and **all 17 demo accounts** (`itmanager`,
`rsantos`, `mlim`, `jvaldez`, `pcruz`, and the twelve intern logins).

**What it keeps:** your own administrator account, the 17 standard shift
templates, and all 39 configuration settings. Those are not demo data — they
are what the system needs to run.

**What it never touches:** anything you entered yourself. Matching is on the
marker alone, so a real intern added alongside the demo survives, as does any
school you created.

Safe to run twice; the second run reports `{}`.

### Check it worked

```bash
python -m app.cli status
```

Interns should read 0, shift templates 17. If any demo account somehow
remains, it can no longer be reached with the demo password — but confirm
with:

```bash
python -m app.cli diagnose
```

which lists every account on the system.

> **Why this matters.** The demo password is published in this repository. An
> earlier version of `purge-demo` removed interns and attendance but left
> every account, school and team behind, so a system that looked clean still
> had a documented password signing in as an IT Manager. If you set this up
> before that was fixed, run `purge-demo` again now — it is safe on an
> already-purged database — and check the account list with `diagnose`.

# Implementation status

What is built, what is not, and the limitations worth knowing before this goes
anywhere near real intern records.

Last updated: 2026-11-21

---

## Complete and verified

### Attendance engine
- [x] 17 standard hourly shift templates, auto-seeded, each 9h elapsed / 8h net
- [x] Custom shift templates with configurable breaks, grace, overtime ceiling, minimum rest
- [x] Overnight shifts: business date bound to the scheduled start date
- [x] `SHIFT-09` midnight-boundary case (end `00:00`) handled as overnight
- [x] Weekly recurring, rotating, temporary and daily-override assignments with documented precedence
- [x] Per-intern rest days and working days, independent of any Mon–Fri assumption
- [x] Holidays, department-scoped, working or non-working
- [x] Digital timekeeping (intern self-service) and supervisor manual entry
- [x] Duplicate time-in prevention
- [x] Overlapping-session prevention across business dates
- [x] Time-out validation and unusual-session-length detection
- [x] Multiple breaks, paid or unpaid, clipped to the session
- [x] Late detection with per-shift grace; grace also forgives the matching undertime
- [x] Undertime, overtime, and half-day classification
- [x] Never marks absent before the shift ends and grace elapses
- [x] Per-record shift snapshot, so template edits never rewrite history
- [x] Supervisor approval and rejection, with bulk approval
- [x] Correction request workflow with before/after capture

### OJT hours
- [x] Append-only ledger; reverse-and-repost instead of mutation
- [x] Totals derived as `SUM(approved AND NOT reversed AND NOT a reversal row)`
- [x] Double-counting prevented, verified under repeated posting and full rebuild
- [x] Remaining hours, completion percentage capped at 100%, uncapped figure retained
- [x] Configurable daily credit ceiling, with `actual_net` preserved separately
- [x] Pending vs approved hours reported distinctly
- [x] Manual adjustments and reversals, both audited
- [x] Milestones at 25 / 50 / 75 / 90 / 100%
- [x] Historical progress snapshots
- [x] Ledger rebuild repair tool (idempotent)

### The five requested capabilities
- [x] **Live duty board** — 10 states, correct across midnight, with shift handover view
- [x] **Signable timesheets** — daily and monthly, frozen content, SHA-256 signature, tamper detection, revisions, record locking
- [x] **Completion forecast** — dual projection (pace and roster), `school_submission_deadline` risk flags, distinguishes "needs more shifts" from "needs better attendance"
- [x] **Technical exposure tracker** — 10 areas, 4 proficiency levels, targets, per-intern matrix, department gap report
- [x] **Compliance dashboard** — per-team compliance, approval backlog with aging, excessive absences, prioritised alerts

### Frontend (React + TypeScript + Vite + Tailwind)
- [x] Sidebar shell with permission-driven navigation and dark / light themes
- [x] Sign-in, session restore, automatic sign-out on token expiry
- [x] Management dashboard: 10 KPI cards and Recharts analytics
- [x] Live duty board, correct across midnight, auto-refreshing
- [x] Compliance dashboard: alerts, per-team bars, approval backlog, at-risk list
- [x] Intern list with filters; intern detail with progress, milestones and forecast
- [x] Attendance register with inline approve and approve-with-overtime
- [x] Month calendar showing the resolved schedule
- [x] Timesheets: submit, sign, verify signature
- [x] Technical exposure coverage matrix with gap shading
- [x] Reports: preview in-page, export to Excel / CSV / PDF
- [x] Admin settings with the live deployment check
- [x] Intern self-service: time clock, progress, schedule, own attendance
- [x] Verified end to end in a real browser against PostgreSQL (35 UI checks)

### Platform
- [x] 29 tables, normalised, with FKs, unique constraints, check constraints, 114 indexes, soft delete
- [x] Alembic migrations; clean upgrade/downgrade, no schema drift
- [x] Dialect-neutral: SQLite and PostgreSQL from the same models
- [x] `UTCDateTime` column type guaranteeing aware UTC on both dialects
- [x] JWT auth, bcrypt hashing, account lockout, forced password change
- [x] RBAC: 46 permissions, 5 roles, scope enforcement per endpoint
- [x] Self-approval prevention at service *and* API layer
- [x] Fernet field encryption with `MultiFernet` rotation and a rotation command
- [x] Keyed HMAC blind indexes so encrypted columns stay searchable
- [x] Immutable audit trail that redacts encrypted values and password hashes
- [x] 36 configurable computation rules, editable at runtime
- [x] 15 reports in Excel, CSV and PDF
- [x] 89 REST endpoints with OpenAPI documentation
- [x] Deployment self-check endpoint
- [x] CLI for keys, bootstrap, demo data, rotation, day finalisation, status
- [x] 162 tests covering all 20 mandated scenarios, all 17 templates, security and HTTP integration

---

## Not built

### Other gaps
- **Notification delivery.** The `notifications` table, alert types and the
  alert feed on the compliance dashboard exist, but nothing writes
  notifications on a schedule, and there is no email or push transport. Alerts
  are computed on request rather than pushed.
- **Scheduled jobs.** `finalize-day` is a CLI command, not a cron job. Until it
  is scheduled, absences are finalised when a supervisor opens the day or runs
  the command. Suggested: nightly, after the latest shift ends.
- **File uploads.** Profile photos and supporting documents have path columns
  and validation settings, but no upload endpoint.
- **Schedule editing from the UI.** The frontend displays the resolved
  calendar; creating and editing assignments (including drag-and-drop) still
  goes through the API. The endpoints exist with full conflict validation.
- **Intern registration and editing from the UI.** Read-only in the frontend
  today; `POST`/`PATCH /api/interns` exist and are tested.
- **FullCalendar.** The month view is a hand-built grid rather than
  FullCalendar, which keeps the bundle smaller. Swapping it in later is
  contained to one component.
- **Shift handover notes.** The handover *view* exists; a notes field for one
  shift to brief the next is not implemented.
- **Docker packaging.** Deployment is documented for systemd and nginx. The
  specification lists Docker as optional and it is not included.

---

## Known limitations

### Encryption
- **Key loss is unrecoverable.** Losing `FERNET_KEYS` permanently destroys
  intern email, contact numbers and emergency contacts. Back the keys up
  separately from the database.
- **Encrypted columns cannot be searched by substring.** Free-text intern
  search covers name and intern code only. Email lookup is exact-match via the
  blind index.
- **Blind indexes leak equality by design.** Two interns with the same email
  produce the same index. That is the trade-off that makes lookup possible; the
  index key is kept separate from the encryption keys to limit it.
- **Encryption protects data at rest, not a compromised application.** A
  running process holds the keys in memory.

### Computation
- **Rest-day work records as `NO_SCHEDULE`.** Hours are captured correctly, but
  there is no scheduled basis to compare against, so no undertime or overtime is
  derived. A supervisor wanting it treated as a normal shift should add a daily
  override.
- **Overlap detection probes a ±3-day window.** Sessions longer than roughly
  three days would not be compared. The 18-hour maximum session length makes
  this unreachable in practice.
- **Conflict validation probes 28 days ahead** for open-ended assignments. A
  conflict that only arises beyond that window is not detected at save time.
- **The forecast is a projection, not a promise.** It assumes the roster holds
  and recent pace continues. Treat it as a prompt to act, not a guarantee.

### Operational
- **SQLite is single-writer.** Fine for one site with modest concurrency; use
  PostgreSQL for multiple sites or many simultaneous supervisors.
- **No rate limiting on the API.** Account lockout covers password guessing;
  general request throttling should be handled at nginx.
- **Timesheet PDF rendering caps at 3,000 rows.** Larger sets export to Excel
  or CSV.
- **No multi-tenancy.** One department per deployment. Multiple sites are
  modelled as departments and teams, not as separate tenants.

---

## Suggested next steps

1. Schedule `finalize-day` nightly (cron or Task Scheduler), after the latest
   shift ends.
2. Add a notification writer and a transport, so alerts reach supervisors
   instead of waiting to be looked at.
3. Add intern registration and schedule assignment screens to the frontend.
4. Add the upload endpoint for profile photos and supporting documents.
5. Pilot with one team for a full OJT cycle before site-wide rollout, and
   reconcile the signed timesheets against what the schools accept.

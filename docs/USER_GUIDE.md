# User guide

For OJT interns, IT Supervisors and IT Mentors.

---

# For OJT interns

## Signing in

Use the username your supervisor gave you. You will be asked to change your
password on first sign-in.

Five wrong passwords locks the account for 15 minutes. If you are locked out,
ask your supervisor to reset it rather than waiting and guessing again.

## Timing in and out

- **Time in** when you start — `POST /api/attendance/time-in`
- **Time out** when you finish — `POST /api/attendance/time-out`

The server records the time. You cannot set or change it yourself — if you
forget a punch, file a correction request.

**Check `my-status` before you punch.** It shows whether you are already timed
in, and what shift you are scheduled for today.

### If you work an overnight shift

Time in and out as normal. A 22:00–07:00 shift counts as **one** session dated
to the day it started. Timing out at 07:00 the next morning does not split your
hours across two days and does not lose anything.

If you arrive after midnight for a shift that started at 22:00, the system still
records it against the correct night — marked late, but on the right day.

### Breaks

Start and end your break if your department asks you to log it. Your shift has
an unpaid break (usually one hour) which is deducted whether or not you log it.
Logging a *longer* break deducts the longer time, so log it accurately.

## What gets credited

Your shift is nine hours long with a one-hour unpaid break, so a full shift
credits **eight** hours. That is normal and correct, not an error.

Two figures are tracked separately:

- **Rendered** — what you actually worked
- **Creditable** — what counts toward your required OJT hours

They differ when you work beyond your shift without approved overtime, or when a
daily ceiling applies. Your rendered hours are always kept on record.

## Your progress

`GET /api/ojt/progress/me` shows required, approved, pending and remaining
hours, your completion percentage, attendance rate, absences, late arrivals and
your projected completion date.

**Approved and pending are different.** Pending hours are worked but not yet
approved by your supervisor, and they do not count toward completion until they
are. If pending hours are piling up, ask your supervisor to review them.

Milestones: 25% Initial Progress, 50% Halfway, 75% Near Completion, 90% Final
Stage, 100% Completed.

## Requesting a correction

If a punch is wrong or missing: `POST /api/attendance/corrections`, with the
date, what should change, and why.

Your supervisor reviews it. You cannot approve your own correction. Once
approved, your hours are recalculated automatically — nothing is double
counted.

## Leave

`POST /api/leave`, with dates and a reason. Attach a document if you have one.

**Approved leave does not usually count toward your required hours.** It excuses
the absence; it does not render hours. Plan your remaining hours accordingly.

## Timesheets

Your timesheet is generated for the month, you submit it, and your supervisor
signs it.

Once signed it is locked, and the signature can be verified at any time — which
is what makes it acceptable to your school. If something needs correcting after
signing, a new revision is created; the signed copy stays on record.

## Technical exposure

`GET /api/exposure/me` shows the IT areas you have hands-on experience in and
where the gaps are: desktop troubleshooting, Active Directory, networking,
hardware deployment, patching, documentation, projects.

Worth checking. If an area shows no hours three weeks before you finish, ask
your supervisor or mentor for a rotation. Your hours are only half of what an
internship is for.

---

# For IT Supervisors

## Your daily rhythm

1. **Duty board** — `GET /api/dashboard/duty-board`. Act on
   `LATE_NO_TIME_IN` and `MISSING_TIME_OUT`.
2. **Approve attendance** — `GET /api/attendance?approval_status=PENDING`.
   Unapproved hours do not count toward your interns' completion.
3. **Pending corrections and leave** —
   `GET /api/attendance/corrections/pending`, `GET /api/leave?status=PENDING`.
4. **Finalise yesterday** if it is not scheduled —
   `POST /api/attendance/finalize-day`.

## Recording attendance for someone

`POST /api/attendance/manual`, with the business date and the punches.

For an overnight shift, **give the business date of the day the shift started**,
and put the next calendar day in the time-out. The system keeps it as one
session.

You cannot hand-enter your own attendance, and you cannot approve attendance for
an intern account linked to you.

## Approving overtime

Overtime beyond the scheduled shift is detected and recorded but **not credited
until you approve it**. Approve with `approve_overtime: true`, capped at the
shift's maximum.

## Assigning shifts

Use the shift selector — `GET /api/shifts/selector` — which shows all 17
templates with times, overnight flag, break configuration and net working hours.

Validate before saving. If you get a rest-interval warning and the assignment is
genuinely needed, supply a reason to save it as an authorised exception; it is
recorded.

## Signing timesheets

`GET /api/timesheets/pending-signature`, then sign.

You cannot sign until every punched record in the period is approved. Clear the
approvals first — the refusal is protecting your signature.

Signing locks the records and stamps a verifiable hash. Treat it as you would
signing the paper form.

## Who needs attention

`GET /api/dashboard/compliance` gives you, in one view: attendance compliance
against target, your approval backlog and its age, interns with excessive
absences, and interns at risk of missing their school deadline.

The forecast tells you *which* problem you have:

- **AT_RISK, 0 hours short** — the roster is fine, attendance is not. Talk to
  the intern.
- **WILL_MISS_DEADLINE, hours short** — the roster cannot deliver it. Add
  shifts, or talk to the school about the deadline. If
  `recommended_extra_hours_per_week` is implausible, the deadline is the thing
  that has to move.

Catching this in week three is a conversation. Catching it in the final week is
a problem.

---

# For IT Mentors

You have read access to your assigned interns, plus the parts that are yours:

## Logging technical exposure

`POST /api/exposure/logs` — area, what they did, how long, and the proficiency
level: Observed, Assisted, Performed Supervised, Performed Independent.

Include the ticket or project reference where there is one. It makes the record
defensible later, to the school and to the intern.

Exposure hours are **separate** from attendance hours. Logging an activity does
not credit attendance, and crediting attendance does not create exposure. They
answer different questions.

## Verifying and evaluating

Verify activities you supervised. You cannot verify your own, and an intern
cannot verify theirs.

Weekly evaluations — `POST /api/evaluations` — cover technical skill,
attendance, attitude, initiative and communication, plus skills acquired, tasks,
projects, accomplishments and development areas.

## Spotting gaps

`GET /api/exposure/matrix` shows who has touched what. The columns with the most
blanks are where to rotate people next. `department_gaps` lists the areas where
most interns have no exposure at all — that is a staffing pattern worth raising,
not an individual problem.

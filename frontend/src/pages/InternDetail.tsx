import { useParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api } from "../api/client";
import type { InternProgressPage, Intern } from "../api/types";
import {
  Badge,
  ErrorNote,
  KpiCard,
  PageHeader,
  ProgressBar,
  Spinner,
} from "../components/ui";
import { dateOnly, pct, titleCase } from "../lib/format";
import { riskTone } from "../lib/status";

export function InternDetail() {
  const { id } = useParams<{ id: string }>();
  const internId = Number(id);

  const progressQ = useQuery({
    queryKey: ["ojt-progress", internId],
    queryFn: () => api.get<InternProgressPage>(`/api/ojt/progress/${internId}`),
    enabled: Number.isFinite(internId),
  });

  const internQ = useQuery({
    queryKey: ["intern", internId],
    queryFn: () => api.get<Intern>(`/api/interns/${internId}`),
    enabled: Number.isFinite(internId),
  });

  if (progressQ.isLoading) return <Spinner label="Loading OJT progress…" />;
  if (progressQ.error)
    return <ErrorNote error={progressQ.error} onRetry={progressQ.refetch} />;
  if (!progressQ.data) return null;

  const { intern, progress, attendance, forecast, milestones } = progressQ.data;
  const profile = internQ.data;

  return (
    <>
      <PageHeader
        title={intern.name}
        subtitle={`${intern.intern_code} · ${intern.team ?? "no team"} · started ${dateOnly(intern.ojt_start_date)}`}
        actions={
          <Badge className={riskTone(forecast.risk)}>
            {forecast.risk.replace(/_/g, " ")}
          </Badge>
        }
      />

      <section className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <KpiCard
          label="Approved hours"
          value={progress.approved_hours}
          sub={`of ${progress.required_hours} required`}
        />
        <KpiCard
          label="Pending approval"
          value={progress.pending_hours}
          sub="not yet counted"
          tone={progress.pending_hours > 0 ? "warn" : "default"}
        />
        <KpiCard label="Remaining" value={progress.remaining_hours} />
        <KpiCard
          label="Completion"
          value={pct(progress.completion_percentage)}
          sub={progress.milestone ?? undefined}
          tone={progress.is_complete ? "ok" : "default"}
        />
      </section>

      <section className="mt-4 card">
        <div className="mb-2 flex items-baseline justify-between">
          <h2 className="text-sm font-semibold text-label">OJT progress</h2>
          <span className="tnum text-xs text-label-2">
            {progress.approved_hours} / {progress.required_hours} h
            {progress.raw_completion_percentage > 100 ? (
              <span className="ml-2 text-success">
                ({pct(progress.raw_completion_percentage)} rendered)
              </span>
            ) : null}
          </span>
        </div>
        <ProgressBar
          value={progress.completion_percentage}
          tone={progress.is_complete ? "ok" : "primary"}
        />

        <div className="mt-4 flex flex-wrap gap-2">
          {milestones.map((m) => (
            <span
              key={m.threshold}
              className={`rounded-md border px-2.5 py-1 text-xs ${
                m.reached
                  ? "border-success/40 bg-success-soft text-success"
                  : "border-separator text-label-3"
              }`}
            >
              {m.threshold}% {m.label}
            </span>
          ))}
        </div>

        {progress.actual_rendered_hours !== progress.approved_hours ? (
          <p className="mt-3 border-t border-separator pt-3 text-xs text-label-3">
            Actually rendered: {progress.actual_rendered_hours} h. This differs
            from approved hours where overtime was unauthorised or a daily
            credit ceiling applied — the rendered figure is kept on record
            either way.
          </p>
        ) : null}
      </section>

      <section className="mt-4 grid gap-4 xl:grid-cols-2">
        <div className="card">
          <h2 className="mb-4 text-sm font-semibold text-label">
            Attendance summary
          </h2>
          <dl className="grid grid-cols-2 gap-x-6 gap-y-2.5 text-sm">
            <Stat label="Attendance rate" value={pct(attendance.attendance_rate)} />
            <Stat label="Days present" value={attendance.days_present} />
            <Stat
              label="Absences"
              value={attendance.absences}
              tone={attendance.absences > 0 ? "text-danger" : undefined}
            />
            <Stat
              label="Late arrivals"
              value={attendance.late_arrivals}
              tone={attendance.late_arrivals > 0 ? "text-warning" : undefined}
            />
            <Stat label="Undertime days" value={attendance.undertime_instances} />
            <Stat label="Half days" value={attendance.half_days} />
            <Stat
              label="Incomplete records"
              value={attendance.incomplete_records}
              tone={attendance.incomplete_records > 0 ? "text-danger" : undefined}
            />
            <Stat
              label="Approved overtime"
              value={`${attendance.total_overtime_hours} h`}
            />
          </dl>
        </div>

        <div className="card">
          <h2 className="mb-1 text-sm font-semibold text-label">
            Completion forecast
          </h2>
          <p className="mb-4 text-xs text-label-3">
            Projected from recent approved pace and the assigned roster; the
            later of the two is reported.
          </p>
          <dl className="space-y-2.5 text-sm">
            <Stat label="Recent pace" value={`${forecast.avg_hours_per_week} h/week`} />
            <Stat
              label="Projected completion"
              value={dateOnly(forecast.projected_completion_date)}
            />
            <Stat label="Basis" value={titleCase(forecast.projection_basis)} />
            <Stat
              label="School deadline"
              value={dateOnly(forecast.school_submission_deadline)}
            />
            {forecast.days_to_deadline !== null ? (
              <Stat
                label="Days to deadline"
                value={forecast.days_to_deadline}
                tone={forecast.days_to_deadline < 14 ? "text-warning" : undefined}
              />
            ) : null}
            {forecast.days_late ? (
              <Stat
                label="Projected late by"
                value={`${forecast.days_late} days`}
                tone="text-danger"
              />
            ) : null}
            {forecast.hours_short_at_deadline ? (
              <Stat
                label="Roster short by"
                value={`${forecast.hours_short_at_deadline} h`}
                tone="text-danger"
              />
            ) : null}
            {forecast.recommended_extra_hours_per_week ? (
              <Stat
                label="Needs"
                value={`+${forecast.recommended_extra_hours_per_week} h/week`}
                tone="text-warning"
              />
            ) : null}
          </dl>

          {forecast.notes.length ? (
            <ul className="mt-3 space-y-1.5 border-t border-separator pt-3">
              {forecast.notes.map((n, i) => (
                <li key={i} className="text-xs leading-relaxed text-label-2">
                  {n}
                </li>
              ))}
            </ul>
          ) : null}
        </div>
      </section>

      {profile ? (
        <section className="mt-4 card">
          <h2 className="mb-4 text-sm font-semibold text-label">Profile</h2>
          <dl className="grid gap-x-8 gap-y-2.5 text-sm sm:grid-cols-2 lg:grid-cols-3">
            <Stat label="Full name" value={profile.full_name} />
            <Stat label="School" value={profile.school_name ?? "—"} />
            <Stat label="Course" value={profile.course ?? "—"} />
            <Stat label="Year level" value={profile.year_level ?? "—"} />
            <Stat label="Email" value={profile.email ?? "—"} />
            <Stat label="Contact" value={profile.contact_number ?? "—"} />
            <Stat
              label="Emergency contact"
              value={
                profile.emergency_contact_name
                  ? `${profile.emergency_contact_name} (${profile.emergency_contact_number ?? "no number"})`
                  : "—"
              }
            />
            <Stat label="Supervisor" value={intern.supervisor ?? "—"} />
            <Stat label="Mentor" value={intern.mentor ?? "—"} />
            <Stat
              label="Expected completion"
              value={dateOnly(profile.expected_completion_date)}
            />
          </dl>
          <div className="mt-4 border-t border-separator pt-4">
            <h3 className="label-section">
              School&rsquo;s OJT / IT coordinator
            </h3>
            <dl className="grid gap-x-8 gap-y-2.5 text-ios-footnote sm:grid-cols-2 lg:grid-cols-3">
              <Stat label="Coordinator" value={profile.coordinator_name ?? "—"} />
              <Stat
                label="Coordinator email"
                value={profile.coordinator_email ?? "Not on file"}
              />
              <Stat
                label="Coordinator contact"
                value={profile.coordinator_contact ?? "—"}
              />
            </dl>
            {!profile.coordinator_email ? (
              <p className="mt-2 rounded-ios border border-warning/30 bg-warning-soft p-2.5 text-ios-caption text-warning">
                No coordinator email on file. The signed timesheet has to be
                returned to the school when this placement ends — add it under
                Schools.
              </p>
            ) : null}
          </div>

          <p className="mt-3 border-t border-separator pt-3 text-ios-caption text-label-3">
            Email, contact and emergency details are stored encrypted; they are
            decrypted only for authorised readers. The coordinator&rsquo;s
            details are an institutional contact and are not encrypted, because
            they are printed on the timesheet the school receives.
          </p>
        </section>
      ) : null}
    </>
  );
}

function Stat({
  label,
  value,
  tone,
}: {
  label: string;
  value: string | number;
  tone?: string;
}) {
  return (
    <div className="flex justify-between gap-4 border-b border-separator/60 pb-2">
      <dt className="text-label-2">{label}</dt>
      <dd className={`tnum text-right ${tone ?? "text-label"}`}>{value}</dd>
    </div>
  );
}

import { useQuery } from "@tanstack/react-query";
import { api } from "../api/client";
import type { InternProgressPage } from "../api/types";
import {
  Badge,
  ErrorNote,
  KpiCard,
  PageHeader,
  ProgressBar,
  Spinner,
} from "../components/ui";
import { dateOnly, pct } from "../lib/format";
import { riskTone } from "../lib/status";

export function MyProgress() {
  const { data, isLoading, error, refetch } = useQuery({
    queryKey: ["my-progress"],
    queryFn: () => api.get<InternProgressPage>("/api/ojt/progress/me"),
  });

  if (isLoading) return <Spinner label="Loading your progress…" />;
  if (error) return <ErrorNote error={error} onRetry={refetch} />;
  if (!data) return null;

  const { progress, attendance, forecast, milestones } = data;

  return (
    <>
      <PageHeader
        title="My OJT Progress"
        subtitle={`${data.intern.intern_code} · started ${dateOnly(data.intern.ojt_start_date)}`}
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
          tone="ok"
        />
        <KpiCard
          label="Awaiting approval"
          value={progress.pending_hours}
          sub="does not count yet"
          tone={progress.pending_hours > 0 ? "warn" : "default"}
        />
        <KpiCard label="Remaining" value={progress.remaining_hours} />
        <KpiCard
          label="Completion"
          value={pct(progress.completion_percentage)}
          sub={progress.milestone ?? "not yet at 25%"}
        />
      </section>

      <section className="mt-4 card">
        <div className="mb-2 flex items-baseline justify-between">
          <h2 className="text-sm font-semibold text-slate-200">
            Toward {progress.required_hours} hours
          </h2>
          <span className="tnum text-xs text-slate-400">
            {progress.approved_hours} h approved
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
                  : "border-edge text-slate-500"
              }`}
            >
              {m.threshold}% {m.label}
            </span>
          ))}
        </div>
        {progress.next_milestone ? (
          <p className="mt-3 text-xs text-slate-500">
            Next milestone: {progress.next_milestone.label} at{" "}
            {progress.next_milestone.threshold}%.
          </p>
        ) : null}
      </section>

      <section className="mt-4 grid gap-4 lg:grid-cols-2">
        <div className="card">
          <h2 className="mb-4 text-sm font-semibold text-slate-200">
            Your attendance
          </h2>
          <dl className="grid grid-cols-2 gap-x-6 gap-y-2.5 text-sm">
            <Row label="Attendance rate" value={pct(attendance.attendance_rate)} />
            <Row label="Days present" value={attendance.days_present} />
            <Row label="Absences" value={attendance.absences} />
            <Row label="Late arrivals" value={attendance.late_arrivals} />
            <Row label="Undertime days" value={attendance.undertime_instances} />
            <Row
              label="Approved overtime"
              value={`${attendance.total_overtime_hours} h`}
            />
          </dl>
          {attendance.incomplete_records > 0 ? (
            <p className="mt-3 rounded-lg border border-warning/40 bg-warning-soft px-3 py-2 text-xs text-warning">
              You have {attendance.incomplete_records} session
              {attendance.incomplete_records === 1 ? "" : "s"} with no time out.
              Those credit zero hours until corrected — file a correction
              request.
            </p>
          ) : null}
        </div>

        <div className="card">
          <h2 className="mb-1 text-sm font-semibold text-slate-200">
            Will you finish in time?
          </h2>
          <p className="mb-4 text-xs text-slate-500">
            Based on your recent pace and your assigned shifts.
          </p>
          <dl className="space-y-2.5 text-sm">
            <Row label="Your pace" value={`${forecast.avg_hours_per_week} h/week`} />
            <Row
              label="Projected finish"
              value={dateOnly(forecast.projected_completion_date)}
            />
            <Row
              label="School deadline"
              value={dateOnly(forecast.school_submission_deadline)}
            />
            {forecast.days_to_deadline !== null ? (
              <Row label="Days left" value={forecast.days_to_deadline} />
            ) : null}
          </dl>
          {forecast.notes.length ? (
            <ul className="mt-3 space-y-1.5 border-t border-edge pt-3">
              {forecast.notes.map((n, i) => (
                <li key={i} className="text-xs leading-relaxed text-slate-400">
                  {n}
                </li>
              ))}
            </ul>
          ) : null}
        </div>
      </section>
    </>
  );
}

function Row({ label, value }: { label: string; value: string | number }) {
  return (
    <div className="flex justify-between gap-4 border-b border-edge/60 pb-2">
      <dt className="text-slate-400">{label}</dt>
      <dd className="tnum text-slate-200">{value}</dd>
    </div>
  );
}

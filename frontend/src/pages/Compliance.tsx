import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "../api/client";
import type { ComplianceDashboard } from "../api/types";
import {
  Badge,
  Empty,
  ErrorNote,
  KpiCard,
  PageHeader,
  ProgressBar,
  Spinner,
  Table,
} from "../components/ui";
import { dateOnly, pct } from "../lib/format";
import { backlogTone, riskTone } from "../lib/status";

const PRESETS = ["THIS_WEEK", "THIS_MONTH", "THIS_QUARTER"];

export function Compliance() {
  const [preset, setPreset] = useState("THIS_MONTH");

  const { data, isLoading, error, refetch } = useQuery({
    queryKey: ["compliance", preset],
    queryFn: () =>
      api.get<ComplianceDashboard>("/api/dashboard/compliance", { preset }),
  });

  if (isLoading) return <Spinner label="Loading compliance…" />;
  if (error) return <ErrorNote error={error} onRetry={refetch} />;
  if (!data) return null;

  const s = data.summary;

  return (
    <>
      <PageHeader
        title="Management Compliance"
        subtitle={`${data.filter.date_from} to ${data.filter.date_to} · all IT teams`}
        actions={
          <div className="flex gap-1 rounded-ios border border-separator p-1">
            {PRESETS.map((p) => (
              <button
                key={p}
                onClick={() => setPreset(p)}
                className={`rounded px-2.5 py-1 text-xs ${
                  preset === p
                    ? "bg-primary text-white"
                    : "text-label-2 hover:text-label"
                }`}
              >
                {p.replace("THIS_", "").toLowerCase()}
              </button>
            ))}
          </div>
        }
      />

      {data.alerts.length ? (
        <section className="mb-5 space-y-2">
          {data.alerts.map((a, i) => (
            <div
              key={i}
              className={`flex items-start gap-3 rounded-ios border px-4 py-2.5 text-sm ${
                a.severity === "CRITICAL"
                  ? "border-danger/40 bg-danger-soft text-danger"
                  : "border-warning/40 bg-warning-soft text-warning"
              }`}
            >
              <span className="mt-0.5 text-xs font-bold">
                {a.severity === "CRITICAL" ? "!" : "•"}
              </span>
              <p className="text-label">{a.message}</p>
            </div>
          ))}
        </section>
      ) : null}

      <section className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <KpiCard
          label="Attendance compliance"
          value={pct(s.attendance_compliance_percentage)}
          sub={`target ${s.attendance_target}%`}
          tone={s.meets_attendance_target ? "ok" : "warn"}
        />
        <KpiCard label="Punctuality" value={pct(s.punctuality_percentage)} />
        <KpiCard
          label="OJT completion"
          value={pct(s.ojt_completion_percentage)}
          sub={`${s.active_interns} active interns`}
        />
        <KpiCard
          label="At risk of missing deadline"
          value={s.interns_at_risk}
          tone={s.interns_at_risk > 0 ? "bad" : "ok"}
        />
      </section>

      <section className="mt-3 grid grid-cols-2 gap-3 lg:grid-cols-4">
        <KpiCard
          label="Pending approvals"
          value={s.total_pending_approvals}
          sub="holding back OJT credit"
          tone={s.total_pending_approvals > 0 ? "warn" : "default"}
        />
        <KpiCard
          label="Overdue approvals"
          value={s.overdue_approvals}
          tone={s.overdue_approvals > 0 ? "bad" : "default"}
        />
        <KpiCard
          label="Supervisors behind"
          value={s.supervisors_behind}
          tone={s.supervisors_behind > 0 ? "warn" : "ok"}
        />
        <KpiCard
          label="Excessive absences"
          value={s.interns_with_excessive_absences}
          tone={s.interns_with_excessive_absences > 0 ? "warn" : "default"}
        />
      </section>

      {data.at_risk_interns.length ? (
        <section className="mt-6 card">
          <h2 className="mb-1 text-sm font-semibold text-label">
            Interns at risk
          </h2>
          <p className="mb-4 text-xs text-label-3">
            &ldquo;Will miss deadline&rdquo; with hours short means the roster
            itself cannot deliver it — add shifts. &ldquo;At risk&rdquo; with
            nothing short means the schedule covers it but attendance does not.
          </p>
          <Table
            head={
              <tr>
                <th className="th">Intern</th>
                <th className="th">Risk</th>
                <th className="th text-right">Completion</th>
                <th className="th text-right">Remaining</th>
                <th className="th text-right">Pace</th>
                <th className="th">Projected</th>
                <th className="th">School deadline</th>
                <th className="th text-right">Short by</th>
                <th className="th text-right">Needs</th>
              </tr>
            }
          >
            {data.at_risk_interns.map((f) => (
              <tr key={f.intern_id} className="hover:bg-fill">
                <td className="td">
                  <a
                    className="font-medium text-label hover:text-primary"
                    href={`/interns/${f.intern_id}`}
                  >
                    {f.intern_name}
                  </a>
                  <span className="ml-2 text-xs text-label-3">
                    {f.intern_code}
                  </span>
                </td>
                <td className="td">
                  <Badge className={riskTone(f.risk)}>
                    {f.risk.replace(/_/g, " ")}
                  </Badge>
                </td>
                <td className="td tnum text-right">{pct(f.completion_percentage)}</td>
                <td className="td tnum text-right">{f.remaining_hours} h</td>
                <td className="td tnum text-right text-label-2">
                  {f.avg_hours_per_week} h/wk
                </td>
                <td className="td">{dateOnly(f.projected_completion_date)}</td>
                <td className="td">
                  {dateOnly(f.school_submission_deadline)}
                  {f.days_late ? (
                    <span className="ml-2 text-xs text-danger">
                      +{f.days_late}d
                    </span>
                  ) : null}
                </td>
                <td className="td tnum text-right">
                  {f.hours_short_at_deadline ? `${f.hours_short_at_deadline} h` : "—"}
                </td>
                <td className="td tnum text-right">
                  {f.recommended_extra_hours_per_week
                    ? `+${f.recommended_extra_hours_per_week} h/wk`
                    : "—"}
                </td>
              </tr>
            ))}
          </Table>
        </section>
      ) : null}

      <section className="mt-4 grid gap-4 xl:grid-cols-2">
        <div className="card">
          <h2 className="mb-4 text-sm font-semibold text-label">
            Compliance by team
          </h2>
          {data.by_team.length ? (
            <div className="space-y-3">
              {data.by_team.map((t) => (
                <div key={t.team_id}>
                  <div className="mb-1 flex items-baseline justify-between text-xs">
                    <span className="text-label-2">
                      {t.team_name}
                      <span className="ml-2 text-label-3">
                        {t.intern_count} intern{t.intern_count === 1 ? "" : "s"}
                      </span>
                    </span>
                    <span className="tnum text-label-2">
                      {pct(t.compliance_percentage)}
                      {t.absent_days ? ` · ${t.absent_days} absent` : ""}
                    </span>
                  </div>
                  <ProgressBar
                    value={t.compliance_percentage}
                    tone={t.meets_target ? "ok" : "warn"}
                  />
                </div>
              ))}
            </div>
          ) : (
            <Empty title="No teams configured" />
          )}
        </div>

        <div className="card">
          <h2 className="mb-1 text-sm font-semibold text-label">
            Approval backlog
          </h2>
          <p className="mb-4 text-xs text-label-3">
            Unapproved attendance does not count toward OJT hours, so a backlog
            quietly holds back completion.
          </p>
          {data.approval_backlog.supervisors.length ? (
            <Table
              head={
                <tr>
                  <th className="th">Supervisor</th>
                  <th className="th text-right">Interns</th>
                  <th className="th text-right">Pending</th>
                  <th className="th text-right">Overdue</th>
                  <th className="th text-right">Oldest</th>
                  <th className="th">Status</th>
                </tr>
              }
            >
              {data.approval_backlog.supervisors.map((s2) => (
                <tr key={s2.supervisor_id}>
                  <td className="td">{s2.supervisor_name}</td>
                  <td className="td tnum text-right text-label-2">
                    {s2.intern_count}
                  </td>
                  <td className="td tnum text-right">{s2.total_pending}</td>
                  <td className="td tnum text-right">
                    {s2.overdue_items || "—"}
                  </td>
                  <td className="td tnum text-right text-label-2">
                    {s2.oldest_pending_days !== null
                      ? `${s2.oldest_pending_days}d`
                      : "—"}
                  </td>
                  <td className="td">
                    <Badge className={backlogTone(s2.status)}>{s2.status}</Badge>
                  </td>
                </tr>
              ))}
            </Table>
          ) : (
            <Empty title="No supervisors configured" />
          )}
        </div>
      </section>

      {data.excessive_absences.length ? (
        <section className="mt-4 card">
          <h2 className="mb-4 text-sm font-semibold text-label">
            Excessive absences
          </h2>
          <Table
            head={
              <tr>
                <th className="th">Intern</th>
                <th className="th">Team</th>
                <th className="th">Supervisor</th>
                <th className="th text-right">Absences</th>
                <th className="th text-right">Threshold</th>
                <th className="th text-right">Window</th>
              </tr>
            }
          >
            {data.excessive_absences.map((a) => (
              <tr key={a.intern_id}>
                <td className="td">
                  {a.intern_name}
                  <span className="ml-2 text-xs text-label-3">
                    {a.intern_code}
                  </span>
                </td>
                <td className="td text-label-2">{a.team ?? "—"}</td>
                <td className="td text-label-2">{a.supervisor ?? "—"}</td>
                <td className="td tnum text-right text-danger">{a.absences}</td>
                <td className="td tnum text-right text-label-2">
                  {a.threshold}
                </td>
                <td className="td tnum text-right text-label-2">
                  {a.window_days}d
                </td>
              </tr>
            ))}
          </Table>
        </section>
      ) : null}
    </>
  );
}

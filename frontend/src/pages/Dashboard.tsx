import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { api } from "../api/client";
import type { Dashboard as DashboardData } from "../api/types";
import {
  Empty,
  ErrorNote,
  KpiCard,
  PageHeader,
  ProgressBar,
  Spinner,
  Table,
} from "../components/ui";
import { dayLabel, pct } from "../lib/format";

const PRESETS = [
  { key: "TODAY", label: "Today" },
  { key: "THIS_WEEK", label: "This week" },
  { key: "THIS_MONTH", label: "This month" },
  { key: "THIS_QUARTER", label: "This quarter" },
];

const AXIS = { stroke: "#64748b", fontSize: 11 };
const GRID = "#252C3A";

const TOOLTIP_STYLE = {
  background: "#151D2B",
  border: "1px solid #252C3A",
  borderRadius: 8,
  fontSize: 12,
  color: "#e2e8f0",
};

export function Dashboard() {
  const [preset, setPreset] = useState("THIS_MONTH");

  const { data, isLoading, error, refetch } = useQuery({
    queryKey: ["dashboard", preset],
    queryFn: () => api.get<DashboardData>("/api/dashboard", { preset }),
    refetchInterval: 60_000,
  });

  if (isLoading) return <Spinner label="Loading dashboard…" />;
  if (error) return <ErrorNote error={error} onRetry={refetch} />;
  if (!data) return null;

  const k = data.kpi;
  const vs = data.analytics.hours_rendered_vs_required;
  const compliant = k.attendance_compliance_percentage >= 95;

  return (
    <>
      <PageHeader
        title="Management Dashboard"
        subtitle={`${data.filter.date_from} to ${data.filter.date_to} · Asia/Manila`}
        actions={
          <div className="flex gap-1 rounded-ios border border-separator p-1">
            {PRESETS.map((p) => (
              <button
                key={p.key}
                onClick={() => setPreset(p.key)}
                className={`rounded px-2.5 py-1 text-xs transition ${
                  preset === p.key
                    ? "bg-primary text-white"
                    : "text-label-2 hover:text-label"
                }`}
              >
                {p.label}
              </button>
            ))}
          </div>
        }
      />

      <section className="grid grid-cols-2 gap-3 lg:grid-cols-5">
        <KpiCard label="Active interns" value={k.total_active_interns} />
        <KpiCard label="On duty now" value={k.total_on_duty} tone="ok" />
        <KpiCard label="Present today" value={k.total_present_today} />
        <KpiCard
          label="Late today"
          value={k.total_late_today}
          tone={k.total_late_today > 0 ? "warn" : "default"}
        />
        <KpiCard
          label="Absent today"
          value={k.total_absent_today}
          tone={k.total_absent_today > 0 ? "bad" : "default"}
        />
      </section>

      <section className="mt-3 grid grid-cols-2 gap-3 lg:grid-cols-5">
        <KpiCard
          label="Hours rendered"
          value={k.total_hours_rendered.toLocaleString()}
          sub="approved, all time"
        />
        <KpiCard
          label="Pending approval"
          value={k.pending_approval_hours.toLocaleString()}
          sub="not yet counted"
          tone={k.pending_approval_hours > 0 ? "warn" : "default"}
        />
        <KpiCard
          label="Remaining hours"
          value={k.total_remaining_hours.toLocaleString()}
        />
        <KpiCard
          label="Overall completion"
          value={pct(k.overall_completion_percentage)}
        />
        <KpiCard
          label="Attendance compliance"
          value={pct(k.attendance_compliance_percentage)}
          sub={compliant ? "meets 95% target" : "below 95% target"
          }
          tone={compliant ? "ok" : "warn"}
        />
      </section>

      <section className="mt-6 grid gap-4 xl:grid-cols-3">
        <div className="card xl:col-span-2">
          <h2 className="mb-1 text-sm font-semibold text-label">
            Daily attendance
          </h2>
          <p className="mb-4 text-xs text-label-3">
            Present includes late and undertime days; those are counted again in
            the Late series.
          </p>
          {data.analytics.daily_attendance.length ? (
            <ResponsiveContainer width="100%" height={240}>
              <BarChart data={data.analytics.daily_attendance}>
                <CartesianGrid stroke={GRID} vertical={false} />
                <XAxis
                  dataKey="date"
                  tickFormatter={dayLabel}
                  tick={AXIS}
                  tickLine={false}
                />
                <YAxis tick={AXIS} tickLine={false} allowDecimals={false} />
                <Tooltip
                  contentStyle={TOOLTIP_STYLE}
                  labelFormatter={(v) => dayLabel(String(v))}
                />
                <Legend wrapperStyle={{ fontSize: 11 }} />
                <Bar dataKey="present" name="Present" fill="#22C55E" stackId="a" />
                <Bar dataKey="absent" name="Absent" fill="#EF4444" stackId="a" />
                <Bar dataKey="other" name="Leave / rest" fill="#475569" stackId="a" />
                <Bar dataKey="late" name="Late" fill="#F59E0B" />
              </BarChart>
            </ResponsiveContainer>
          ) : (
            <Empty
              title="No attendance in this period"
              hint="Pick a wider date range, or seed demo data."
            />
          )}
        </div>

        <div className="card">
          <h2 className="mb-1 text-sm font-semibold text-label">
            Hours rendered vs required
          </h2>
          <p className="mb-4 text-xs text-label-3">
            Across all active interns, capped per intern at their requirement.
          </p>
          <div className="space-y-4">
            <div>
              <div className="mb-1.5 flex justify-between text-xs">
                <span className="text-label-2">Rendered</span>
                <span className="tnum text-label">
                  {vs.rendered_hours.toLocaleString()} h
                </span>
              </div>
              <ProgressBar
                value={
                  vs.required_hours
                    ? (vs.rendered_hours / vs.required_hours) * 100
                    : 0
                }
                tone="ok"
              />
            </div>
            <dl className="space-y-2 text-sm">
              <Row label="Required" value={`${vs.required_hours.toLocaleString()} h`} />
              <Row
                label="Remaining"
                value={`${vs.remaining_hours.toLocaleString()} h`}
              />
              <Row
                label="Completed OJT"
                value={String(k.total_completed_ojt)}
              />
            </dl>
          </div>
        </div>
      </section>

      <section className="mt-4 grid gap-4 xl:grid-cols-2">
        <div className="card">
          <h2 className="mb-4 text-sm font-semibold text-label">
            Approved hours per day
          </h2>
          {data.analytics.hours_per_day.length ? (
            <ResponsiveContainer width="100%" height={220}>
              <LineChart data={data.analytics.hours_per_day}>
                <CartesianGrid stroke={GRID} vertical={false} />
                <XAxis
                  dataKey="date"
                  tickFormatter={dayLabel}
                  tick={AXIS}
                  tickLine={false}
                />
                <YAxis tick={AXIS} tickLine={false} />
                <Tooltip
                  contentStyle={TOOLTIP_STYLE}
                  labelFormatter={(v) => dayLabel(String(v))}
                  formatter={(v) => [`${v} h`, "Approved"]}
                />
                <Line
                  type="monotone"
                  dataKey="hours"
                  stroke="#3B82F6"
                  strokeWidth={2}
                  dot={false}
                />
              </LineChart>
            </ResponsiveContainer>
          ) : (
            <Empty title="No approved hours in this period" />
          )}
        </div>

        <div className="card">
          <h2 className="mb-1 text-sm font-semibold text-label">
            Compliance by team
          </h2>
          <p className="mb-4 text-xs text-label-3">
            Reported days divided by days actually expected. Rest days, holidays
            and approved leave are excluded.
          </p>
          {data.analytics.attendance_by_team.length ? (
            <ResponsiveContainer width="100%" height={220}>
              <BarChart
                data={data.analytics.attendance_by_team}
                layout="vertical"
                margin={{ left: 8 }}
              >
                <CartesianGrid stroke={GRID} horizontal={false} />
                <XAxis
                  type="number"
                  domain={[0, 100]}
                  tick={AXIS}
                  tickLine={false}
                  unit="%"
                />
                <YAxis
                  type="category"
                  dataKey="team_code"
                  tick={AXIS}
                  tickLine={false}
                  width={52}
                />
                <Tooltip
                  contentStyle={TOOLTIP_STYLE}
                  formatter={(v, _n, p) => [
                    `${v}%`,
                    (p?.payload as { team_name?: string })?.team_name ?? "",
                  ]}
                />
                <Bar dataKey="compliance_percentage" radius={[0, 4, 4, 0]}>
                  {data.analytics.attendance_by_team.map((t) => (
                    <Cell
                      key={t.team_id}
                      fill={t.meets_target ? "#22C55E" : "#F59E0B"}
                    />
                  ))}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          ) : (
            <Empty title="No teams configured" />
          )}
        </div>
      </section>

      <section className="mt-4">
        <div className="card">
          <h2 className="mb-4 text-sm font-semibold text-label">
            Individual progress
          </h2>
          {data.analytics.individual_progress.length ? (
            <Table
              head={
                <tr>
                  <th className="th">Intern</th>
                  <th className="th">Team</th>
                  <th className="th text-right">Approved</th>
                  <th className="th text-right">Required</th>
                  <th className="th text-right">Remaining</th>
                  <th className="th w-48">Completion</th>
                  <th className="th">Milestone</th>
                </tr>
              }
            >
              {data.analytics.individual_progress.map((r) => (
                <tr key={r.intern_id} className="hover:bg-fill">
                  <td className="td">
                    <a
                      className="font-medium text-label hover:text-primary"
                      href={`/interns/${r.intern_id}`}
                    >
                      {r.intern_name}
                    </a>
                    <span className="ml-2 text-xs text-label-3">
                      {r.intern_code}
                    </span>
                  </td>
                  <td className="td text-label-2">{r.team ?? "—"}</td>
                  <td className="td tnum text-right">{r.approved_hours}</td>
                  <td className="td tnum text-right text-label-2">
                    {r.required_hours}
                  </td>
                  <td className="td tnum text-right">{r.remaining_hours}</td>
                  <td className="td">
                    <div className="flex items-center gap-2">
                      <ProgressBar
                        value={r.completion_percentage}
                        tone={
                          r.completion_percentage >= 90
                            ? "ok"
                            : r.completion_percentage >= 50
                              ? "primary"
                              : "warn"
                        }
                      />
                      <span className="tnum w-12 shrink-0 text-right text-xs text-label-2">
                        {r.completion_percentage}%
                      </span>
                    </div>
                  </td>
                  <td className="td text-xs text-label-2">
                    {r.milestone ?? "—"}
                  </td>
                </tr>
              ))}
            </Table>
          ) : (
            <Empty title="No active interns" />
          )}
        </div>
      </section>
    </>
  );
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex justify-between border-t border-separator pt-2">
      <dt className="text-label-2">{label}</dt>
      <dd className="tnum text-label">{value}</dd>
    </div>
  );
}

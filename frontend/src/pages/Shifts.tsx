import { useQuery } from "@tanstack/react-query";
import { api } from "../api/client";
import type { ShiftTemplate } from "../api/types";
import { Badge, ErrorNote, PageHeader, Spinner, Table } from "../components/ui";
import { hhmm } from "../lib/format";
import { toneClass } from "../lib/status";

export function Shifts() {
  const { data, isLoading, error, refetch } = useQuery({
    queryKey: ["shifts"],
    queryFn: () => api.get<ShiftTemplate[]>("/api/shifts", { include_inactive: true }),
  });

  if (isLoading) return <Spinner />;
  if (error) return <ErrorNote error={error} onRetry={refetch} />;
  if (!data) return null;

  const day = data.filter((s) => !s.is_overnight);
  const night = data.filter((s) => s.is_overnight);

  return (
    <>
      <PageHeader
        title="Shift Templates"
        subtitle="Editing a template never changes past attendance: each record keeps a snapshot of the configuration that applied when it was created."
      />

      <div className="mb-4 flex flex-wrap gap-3 text-xs text-label-3">
        <span>{data.length} templates</span>
        <span>· {day.length} day shifts</span>
        <span>· {night.length} overnight</span>
      </div>

      <Table
        head={
          <tr>
            <th className="th">Code</th>
            <th className="th">Schedule</th>
            <th className="th">Overnight</th>
            <th className="th text-right">Elapsed</th>
            <th className="th text-right">Break</th>
            <th className="th text-right">Net working</th>
            <th className="th text-right">Grace</th>
            <th className="th text-right">Max OT</th>
            <th className="th text-right">Min rest</th>
            <th className="th">State</th>
          </tr>
        }
      >
        {data.map((s) => (
          <tr key={s.id} className="hover:bg-fill">
            <td className="td font-mono text-xs">
              {s.code}
              {s.is_system_default ? (
                <span className="ml-1.5 text-[10px] text-label-3" title="Standard template">
                  std
                </span>
              ) : null}
            </td>
            <td className="td tnum">
              {s.start_time.slice(0, 5)} – {s.end_time.slice(0, 5)}
            </td>
            <td className="td">
              {s.is_overnight ? (
                <Badge className={toneClass("info")}>crosses midnight</Badge>
              ) : (
                <span className="text-xs text-label-3">no</span>
              )}
            </td>
            <td className="td tnum text-right text-label-2">
              {hhmm(s.elapsed_minutes)}
            </td>
            <td className="td tnum text-right">
              {hhmm(s.break_minutes)}
              <span className="ml-1 text-[10px] text-label-3">
                {s.break_is_paid ? "paid" : "unpaid"}
              </span>
            </td>
            <td className="td tnum text-right font-medium">
              {hhmm(s.net_working_minutes)}
            </td>
            <td className="td tnum text-right text-label-2">
              {s.grace_period_minutes}m
            </td>
            <td className="td tnum text-right text-label-2">
              {s.max_overtime_minutes}m
            </td>
            <td className="td tnum text-right text-label-2">
              {s.min_rest_hours}h
            </td>
            <td className="td">
              <Badge className={toneClass(s.is_active ? "ok" : "muted")}>
                {s.is_active ? "Active" : "Inactive"}
              </Badge>
            </td>
          </tr>
        ))}
      </Table>

      <p className="mt-3 text-xs text-label-3">
        Every standard template is nine hours elapsed with a one-hour unpaid
        break, so a full shift credits eight hours — not nine. SHIFT-09 ends at
        exactly 00:00, which is midnight of the following day and is treated as
        overnight.
      </p>
    </>
  );
}

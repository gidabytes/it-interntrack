import { useQuery } from "@tanstack/react-query";
import { api } from "../api/client";
import type { ExposureMatrix } from "../api/types";
import {
  Empty,
  ErrorNote,
  KpiCard,
  PageHeader,
  Spinner,
  Table,
} from "../components/ui";

/** Heat shading by hours logged, so gaps read at a glance. */
function cellTone(hoursLogged: number, coverage: number): string {
  if (hoursLogged === 0) return "bg-danger-soft text-label-3";
  if (coverage >= 100) return "bg-success-soft text-success";
  if (coverage >= 50) return "bg-primary-soft text-primary";
  return "bg-warning-soft text-warning";
}

export function Exposure() {
  const { data, isLoading, error, refetch } = useQuery({
    queryKey: ["exposure-matrix"],
    queryFn: () => api.get<ExposureMatrix>("/api/exposure/matrix"),
  });

  if (isLoading) return <Spinner label="Loading exposure matrix…" />;
  if (error) return <ErrorNote error={error} onRetry={refetch} />;
  if (!data) return null;

  const fullyCovered = data.interns.filter(
    (i) => i.core_areas_met === i.core_areas_total,
  ).length;

  return (
    <>
      <PageHeader
        title="Technical Exposure"
        subtitle="What interns have actually done, not just that they were present. Exposure hours are tracked independently of OJT hours."
      />

      <section className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <KpiCard label="Interns tracked" value={data.intern_count} />
        <KpiCard
          label="Full core coverage"
          value={`${fullyCovered}/${data.intern_count}`}
          tone={fullyCovered === data.intern_count ? "ok" : "default"}
        />
        <KpiCard
          label="Department gaps"
          value={data.department_gaps.length}
          sub="areas under 50% exposed"
          tone={data.department_gaps.length > 0 ? "warn" : "ok"}
        />
        <KpiCard
          label="Total logged"
          value={data.area_summary
            .reduce((s, a) => s + a.total_hours, 0)
            .toFixed(0)}
          sub="hours of activity"
        />
      </section>

      {data.department_gaps.length ? (
        <section className="mt-4 card">
          <h2 className="mb-1 text-sm font-semibold text-label">
            Where the department is not covering
          </h2>
          <p className="mb-3 text-xs text-label-3">
            Areas most interns have never touched. This is a staffing pattern to
            address with rotations, not an individual shortcoming.
          </p>
          <div className="flex flex-wrap gap-2">
            {data.department_gaps.map((g) => (
              <span
                key={g.area}
                className="rounded-ios border border-warning/40 bg-warning-soft px-3 py-1.5 text-xs text-warning"
              >
                {g.label}
                <span className="ml-2 text-label-2">
                  {g.exposure_rate}% exposed · {g.interns_untouched} untouched
                </span>
              </span>
            ))}
          </div>
        </section>
      ) : null}

      <section className="mt-4 card">
        <h2 className="mb-1 text-sm font-semibold text-label">
          Coverage matrix
        </h2>
        <p className="mb-4 text-xs text-label-3">
          Hours logged per area. Sorted with the least-covered interns first —
          those are the ones to rotate next.
        </p>

        {data.interns.length ? (
          <Table
            head={
              <tr>
                <th className="th">Intern</th>
                <th className="th">Team</th>
                {data.areas.map((a) => (
                  <th key={a} className="th text-center">
                    <span className="block max-w-[5.5rem] whitespace-normal leading-tight">
                      {a}
                    </span>
                  </th>
                ))}
                <th className="th text-right">Total</th>
                <th className="th text-center">Core met</th>
              </tr>
            }
          >
            {data.interns.map((row) => (
              <tr key={row.intern_id} className="hover:bg-fill">
                <td className="td">
                  <a
                    className="font-medium text-label hover:text-primary"
                    href={`/interns/${row.intern_id}`}
                  >
                    {row.intern_name}
                  </a>
                  <span className="ml-2 text-xs text-label-3">
                    {row.intern_code}
                  </span>
                </td>
                <td className="td text-xs text-label-2">{row.team ?? "—"}</td>
                {data.area_keys.map((k) => {
                  const cell = row.areas[k];
                  return (
                    <td key={k} className="td text-center">
                      <span
                        className={`tnum inline-block min-w-[2.75rem] rounded px-1.5 py-0.5 text-xs ${cellTone(
                          cell?.hours ?? 0,
                          cell?.coverage_percentage ?? 0,
                        )}`}
                        title={
                          cell?.proficiency
                            ? `Best proficiency: ${cell.proficiency.replace(/_/g, " ")}`
                            : "No activity logged"
                        }
                      >
                        {cell?.hours ? cell.hours.toFixed(1) : "—"}
                      </span>
                    </td>
                  );
                })}
                <td className="td tnum text-right font-medium">
                  {row.total_logged_hours.toFixed(1)}
                </td>
                <td className="td text-center">
                  <span
                    className={`tnum text-xs ${
                      row.core_areas_met === row.core_areas_total
                        ? "text-success"
                        : row.core_areas_met === 0
                          ? "text-danger"
                          : "text-warning"
                    }`}
                  >
                    {row.core_areas_met}/{row.core_areas_total}
                  </span>
                </td>
              </tr>
            ))}
          </Table>
        ) : (
          <Empty title="No interns to show" />
        )}

        <div className="mt-4 flex flex-wrap gap-3 border-t border-separator pt-3 text-[11px] text-label-3">
          <span className="flex items-center gap-1.5">
            <span className="h-2.5 w-2.5 rounded bg-success-soft" /> target met
          </span>
          <span className="flex items-center gap-1.5">
            <span className="h-2.5 w-2.5 rounded bg-primary-soft" /> over halfway
          </span>
          <span className="flex items-center gap-1.5">
            <span className="h-2.5 w-2.5 rounded bg-warning-soft" /> some exposure
          </span>
          <span className="flex items-center gap-1.5">
            <span className="h-2.5 w-2.5 rounded bg-danger-soft" /> never touched
          </span>
        </div>
      </section>
    </>
  );
}

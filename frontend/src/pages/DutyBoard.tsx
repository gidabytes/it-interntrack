import { useQuery } from "@tanstack/react-query";
import { api } from "../api/client";
import type { DutyBoard as Board, DutyEntry } from "../api/types";
import { Badge, Empty, ErrorNote, KpiCard, PageHeader, Spinner } from "../components/ui";
import { clock, duration } from "../lib/format";
import { DUTY_ORDER, dutyTone } from "../lib/status";

export function DutyBoard() {
  const { data, isLoading, error, refetch, dataUpdatedAt } = useQuery({
    queryKey: ["duty-board"],
    queryFn: () => api.get<Board>("/api/dashboard/duty-board"),
    // An operations board is only useful if it is current.
    refetchInterval: 30_000,
  });

  if (isLoading) return <Spinner label="Loading duty board…" />;
  if (error) return <ErrorNote error={error} onRetry={refetch} />;
  if (!data) return null;

  return (
    <>
      <PageHeader
        title="Live Duty Board"
        subtitle={
          `Across all shifts · ${data.window.from} to ${data.window.to} · ` +
          `refreshed ${clock(new Date(dataUpdatedAt).toISOString())}`
        }
        actions={
          <button className="btn-ghost" onClick={() => refetch()}>
            Refresh
          </button>
        }
      />

      <section className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <KpiCard label="On duty now" value={data.on_duty_now} tone="ok" />
        <KpiCard
          label="Needs attention"
          value={data.needs_attention}
          sub="late, missing time out, absent"
          tone={data.needs_attention > 0 ? "bad" : "default"}
        />
        <KpiCard
          label="Expected to report"
          value={data.counts.EXPECTED_TO_REPORT ?? 0}
        />
        <KpiCard label="Completed shift" value={data.counts.COMPLETED_SHIFT ?? 0} />
      </section>

      <p className="mt-4 text-xs text-slate-500">
        An overnight shift belongs to the date it started, so at 02:00 the
        interns on duty show yesterday&apos;s business date. That is correct, not a
        display error.
      </p>

      <div className="mt-4 space-y-4">
        {DUTY_ORDER.map(({ state, label, hint }) => {
          const rows = data.buckets[state as keyof Board["buckets"]] ?? [];
          if (!rows.length) return null;
          return (
            <section key={state} className="card">
              <div className="mb-3 flex items-baseline justify-between gap-3">
                <div>
                  <h2 className="text-sm font-semibold text-slate-200">
                    {label}
                    <span className="ml-2 text-xs font-normal text-slate-500">
                      {rows.length}
                    </span>
                  </h2>
                  <p className="text-xs text-slate-500">{hint}</p>
                </div>
                <Badge className={dutyTone(state)}>{state.replace(/_/g, " ")}</Badge>
              </div>

              <div className="grid gap-2 md:grid-cols-2 xl:grid-cols-3">
                {rows.map((r) => (
                  <DutyCard key={`${r.intern_id}-${r.business_date}`} entry={r} />
                ))}
              </div>
            </section>
          );
        })}

        {Object.values(data.buckets).every((b) => !b.length) ? (
          <Empty
            title="Nobody scheduled in this window"
            hint="Assign shifts, or seed demo data to see the board populated."
          />
        ) : null}
      </div>
    </>
  );
}

function DutyCard({ entry: r }: { entry: DutyEntry }) {
  return (
    <div className="rounded-lg border border-edge bg-base/40 p-3">
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <p className="truncate text-sm font-medium text-slate-200">
            {r.intern_name}
          </p>
          <p className="text-xs text-slate-500">
            {r.intern_code}
            {r.team ? ` · ${r.team}` : ""}
          </p>
        </div>
        {r.shift_code ? (
          <span className="shrink-0 rounded border border-edge px-1.5 py-0.5 text-[10px] text-slate-400">
            {r.shift_code}
            {r.is_overnight ? " ⏱" : ""}
          </span>
        ) : null}
      </div>

      <dl className="mt-2.5 space-y-1 text-xs">
        {r.business_date ? (
          <Line label="Business date" value={r.business_date} />
        ) : null}
        {r.scheduled_start && r.scheduled_end ? (
          <Line
            label="Scheduled"
            value={`${clock(r.scheduled_start)} – ${clock(r.scheduled_end)}`}
          />
        ) : null}
        {r.time_in ? <Line label="Time in" value={clock(r.time_in)} /> : null}
        {r.time_out ? <Line label="Time out" value={clock(r.time_out)} /> : null}
        {r.minutes_on_duty > 0 && !r.time_out ? (
          <Line label="On duty" value={duration(r.minutes_on_duty)} />
        ) : null}
        {r.minutes_remaining_in_shift > 0 ? (
          <Line
            label="Shift ends in"
            value={duration(r.minutes_remaining_in_shift)}
          />
        ) : null}
        {r.minutes_until_start > 0 ? (
          <Line label="Due in" value={duration(r.minutes_until_start)} />
        ) : null}
        {r.late_minutes > 0 ? (
          <Line
            label="Late"
            value={duration(r.late_minutes)}
            tone="text-warning"
          />
        ) : null}
      </dl>

      {r.notes.length ? (
        <ul className="mt-2 space-y-0.5 border-t border-edge pt-2">
          {r.notes.map((n, i) => (
            <li key={i} className="text-[11px] leading-snug text-slate-500">
              {n}
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}

function Line({
  label,
  value,
  tone = "text-slate-300",
}: {
  label: string;
  value: string;
  tone?: string;
}) {
  return (
    <div className="flex justify-between gap-2">
      <dt className="text-slate-500">{label}</dt>
      <dd className={`tnum ${tone}`}>{value}</dd>
    </div>
  );
}

import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";
import type { MyStatus } from "../api/types";
import { Badge, ErrorNote, PageHeader, Spinner } from "../components/ui";
import { clock, duration, hhmm } from "../lib/format";
import { attendanceTone } from "../lib/status";

export function MyClock() {
  const qc = useQueryClient();
  const [now, setNow] = useState(() => Date.now());
  const [note, setNote] = useState<string | null>(null);

  // Local ticking clock so the elapsed figure moves without polling the API.
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, []);

  const { data, isLoading, error, refetch } = useQuery({
    queryKey: ["my-status"],
    queryFn: () => api.get<MyStatus>("/api/attendance/my-status"),
    refetchInterval: 60_000,
  });

  function act(path: string, label: string) {
    return () =>
      mutation.mutate({ path, label });
  }

  const mutation = useMutation({
    mutationFn: (v: { path: string; label: string }) =>
      api.post(`/api/attendance/${v.path}`, {}),
    onSuccess: (_d, v) => {
      setNote(`${v.label} recorded.`);
      qc.invalidateQueries({ queryKey: ["my-status"] });
      qc.invalidateQueries({ queryKey: ["my-progress"] });
    },
  });

  if (isLoading) return <Spinner label="Loading your status…" />;
  if (error) return <ErrorNote error={error} onRetry={refetch} />;
  if (!data) return null;

  const sched = data.today_schedule;
  const elapsed = data.open_session?.time_in
    ? Math.floor((now - new Date(data.open_session.time_in).getTime()) / 60000)
    : 0;

  return (
    <>
      <PageHeader
        title="Time Clock"
        subtitle={`${data.intern_code} · server time ${clock(data.server_time)} (Asia/Manila)`}
      />

      <div className="grid gap-4 lg:grid-cols-2">
        <section className="card">
          <h2 className="text-sm font-semibold text-label">
            {data.is_timed_in ? "You are timed in" : "Not timed in"}
          </h2>

          {data.is_timed_in && data.open_session ? (
            <>
              <p className="tnum mt-4 text-4xl font-semibold text-success">
                {duration(elapsed)}
              </p>
              <p className="mt-1 text-xs text-label-3">
                since {clock(data.open_session.time_in)} on{" "}
                {data.open_session.business_date}
                {data.open_session.is_overnight_shift
                  ? " · overnight shift"
                  : ""}
              </p>
              {data.on_break_since ? (
                <p className="mt-3 rounded-ios border border-primary/40 bg-primary-soft px-3 py-2 text-sm text-primary">
                  On break since {clock(data.on_break_since)}
                </p>
              ) : null}
            </>
          ) : (
            <p className="mt-3 text-sm text-label-2">
              {sched.is_working_day
                ? `Scheduled ${sched.shift_code} · ${clock(sched.scheduled_start)} – ${clock(sched.scheduled_end)}`
                : sched.is_rest_day
                  ? "Today is a rest day."
                  : sched.is_on_leave
                    ? "You are on approved leave today."
                    : sched.is_holiday
                      ? `Holiday: ${sched.holiday_name}`
                      : "No shift assigned for today."}
            </p>
          )}

          <div className="mt-5 flex flex-wrap gap-2">
            <button
              className="btn-primary"
              disabled={!data.can_time_in || mutation.isPending}
              onClick={act("time-in", "Time in")}
            >
              Time in
            </button>
            <button
              className="btn-ghost"
              disabled={!data.can_time_out || mutation.isPending}
              onClick={act("time-out", "Time out")}
            >
              Time out
            </button>
            <button
              className="btn-ghost"
              disabled={!data.is_timed_in || !!data.on_break_since || mutation.isPending}
              onClick={act("break/start", "Break start")}
            >
              Start break
            </button>
            <button
              className="btn-ghost"
              disabled={!data.on_break_since || mutation.isPending}
              onClick={act("break/end", "Break end")}
            >
              End break
            </button>
          </div>

          {note ? (
            <p className="mt-4 rounded-ios border border-success/40 bg-success-soft px-3 py-2 text-sm text-success">
              {note}
            </p>
          ) : null}
          {mutation.error ? (
            <div className="mt-4">
              <ErrorNote error={mutation.error} />
            </div>
          ) : null}

          <p className="mt-4 border-t border-separator pt-3 text-xs text-label-3">
            The server records the time; you cannot set it yourself. If a punch
            is missing, file a correction request and your supervisor will
            review it.
          </p>
        </section>

        <section className="card">
          <h2 className="mb-4 text-sm font-semibold text-label">
            Today&apos;s schedule
          </h2>
          <dl className="space-y-2.5 text-sm">
            <Row label="Business date" value={sched.business_date} />
            <Row label="Shift" value={sched.shift_code ?? "—"} />
            <Row
              label="Scheduled"
              value={
                sched.scheduled_start
                  ? `${clock(sched.scheduled_start)} – ${clock(sched.scheduled_end)}`
                  : "—"
              }
            />
            <Row
              label="Overnight"
              value={sched.is_overnight ? "Yes — ends next day" : "No"}
            />
            <Row
              label="Unpaid break"
              value={hhmm(sched.scheduled_break_minutes)}
            />
            <Row
              label="Expected working time"
              value={hhmm(sched.scheduled_net_minutes)}
            />
          </dl>

          {data.open_session ? (
            <div className="mt-4 border-t border-separator pt-4">
              <h3 className="mb-2.5 text-xs font-semibold uppercase tracking-wide text-label-2">
                Current session
              </h3>
              <div className="flex items-center gap-2">
                <Badge className={attendanceTone(data.open_session.status)}>
                  {data.open_session.status.replace(/_/g, " ")}
                </Badge>
                {data.open_session.late_minutes > 0 ? (
                  <span className="text-xs text-warning">
                    {data.open_session.late_minutes} min late
                  </span>
                ) : null}
              </div>
              <p className="mt-2 text-xs text-label-3">
                Hours are credited when you time out, and count toward your OJT
                total once your supervisor approves them.
              </p>
            </div>
          ) : null}
        </section>
      </div>
    </>
  );
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex justify-between gap-4 border-b border-separator/60 pb-2">
      <dt className="text-label-2">{label}</dt>
      <dd className="tnum text-label">{value}</dd>
    </div>
  );
}

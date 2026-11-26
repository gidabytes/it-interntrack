import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "../api/client";
import type { Calendar, ScheduleDay } from "../api/types";
import { ErrorNote, KpiCard, PageHeader, Spinner } from "../components/ui";
import { clock, hhmm, startOfMonth } from "../lib/format";

const WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

function monthBounds(anchor: string) {
  const [y, m] = anchor.slice(0, 7).split("-").map(Number);
  const last = new Date(y, m, 0).getDate();
  return { from: `${anchor.slice(0, 7)}-01`, to: `${anchor.slice(0, 7)}-${last}` };
}

function shiftMonth(anchor: string, delta: number) {
  const [y, m] = anchor.slice(0, 7).split("-").map(Number);
  const d = new Date(y, m - 1 + delta, 1);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-01`;
}

/** Monday-first weekday index, so the grid matches the labels. */
function mondayIndex(isoDate: string): number {
  const [y, m, d] = isoDate.slice(0, 10).split("-").map(Number);
  return (new Date(y, m - 1, d).getDay() + 6) % 7;
}

export function Schedule({ internId }: { internId?: number }) {
  const [anchor, setAnchor] = useState(() =>
    startOfMonth(new Date().toLocaleDateString("en-CA", { timeZone: "Asia/Manila" })),
  );
  const { from, to } = monthBounds(anchor);
  const path = internId
    ? `/api/schedules/calendar/${internId}`
    : "/api/schedules/my-calendar";

  const { data, isLoading, error, refetch } = useQuery({
    queryKey: ["calendar", path, from, to],
    queryFn: () => api.get<Calendar>(path, { date_from: from, date_to: to }),
  });

  const monthLabel = new Date(anchor).toLocaleDateString("en-GB", {
    month: "long",
    year: "numeric",
  });

  return (
    <>
      <PageHeader
        title={internId ? "Schedule" : "My Schedule"}
        subtitle="The shift actually in force for each date, after precedence. Overnight shifts are shown on the day they start."
        actions={
          <div className="flex items-center gap-2">
            <button
              className="btn-ghost px-2.5 py-1.5 text-xs"
              onClick={() => setAnchor(shiftMonth(anchor, -1))}
            >
              ←
            </button>
            <span className="min-w-[8.5rem] text-center text-sm font-medium text-label">
              {monthLabel}
            </span>
            <button
              className="btn-ghost px-2.5 py-1.5 text-xs"
              onClick={() => setAnchor(shiftMonth(anchor, 1))}
            >
              →
            </button>
          </div>
        }
      />

      {isLoading ? (
        <Spinner />
      ) : error ? (
        <ErrorNote error={error} onRetry={refetch} />
      ) : !data ? null : (
        <>
          <section className="grid grid-cols-2 gap-3 lg:grid-cols-5">
            <KpiCard label="Working days" value={data.working_days} />
            <KpiCard label="Scheduled hours" value={data.scheduled_hours} />
            <KpiCard label="Rest days" value={data.rest_days} />
            <KpiCard label="Holidays" value={data.holidays} />
            <KpiCard label="Leave days" value={data.leave_days} />
          </section>

          <div className="mt-5 card">
            <div className="mb-2 grid grid-cols-7 gap-1.5">
              {WEEKDAYS.map((d) => (
                <div
                  key={d}
                  className="pb-1 text-center text-[10px] font-semibold uppercase tracking-wider text-label-3"
                >
                  {d}
                </div>
              ))}
            </div>
            <div className="grid grid-cols-7 gap-1.5">
              {Array.from({ length: mondayIndex(data.days[0]?.business_date ?? from) }).map(
                (_, i) => (
                  <div key={`pad-${i}`} />
                ),
              )}
              {data.days.map((d) => (
                <DayCell key={d.business_date} day={d} />
              ))}
            </div>

            <div className="mt-4 flex flex-wrap gap-3 border-t border-separator pt-3 text-[11px] text-label-3">
              <Legend className="bg-primary/20 border-primary/40" label="Working day" />
              <Legend className="bg-fill border-separator" label="Rest day" />
              <Legend className="bg-warning-soft border-warning/40" label="Holiday" />
              <Legend className="bg-primary-soft border-primary/40" label="Leave" />
              <span>⏱ overnight, ends the next day</span>
            </div>
          </div>
        </>
      )}
    </>
  );
}

function DayCell({ day }: { day: ScheduleDay }) {
  const dayNum = Number(day.business_date.slice(8, 10));

  const tone = day.is_on_leave
    ? "border-primary/40 bg-primary-soft"
    : day.is_holiday
      ? "border-warning/40 bg-warning-soft"
      : day.is_working_day
        ? "border-primary/40 bg-primary/10"
        : "border-separator bg-fill";

  return (
    <div className={`min-h-[4.75rem] rounded-ios border p-1.5 ${tone}`}>
      <div className="flex items-start justify-between">
        <span className="tnum text-xs font-medium text-label-2">{dayNum}</span>
        {day.is_overnight ? (
          <span className="text-[10px]" title="Overnight shift">
            ⏱
          </span>
        ) : null}
      </div>

      {day.is_working_day && day.shift_code ? (
        <div className="mt-1 space-y-0.5">
          <p className="truncate text-[10px] font-medium text-primary">
            {day.shift_code}
          </p>
          <p className="tnum text-[10px] leading-tight text-label-2">
            {clock(day.scheduled_start)}–{clock(day.scheduled_end)}
          </p>
          <p className="tnum text-[10px] text-label-3">
            {hhmm(day.scheduled_net_minutes)}
          </p>
        </div>
      ) : (
        <p className="mt-1 text-[10px] leading-tight text-label-3">
          {day.is_on_leave
            ? "Leave"
            : day.is_holiday
              ? (day.holiday_name ?? "Holiday")
              : day.is_rest_day
                ? "Rest"
                : "—"}
        </p>
      )}
    </div>
  );
}

function Legend({ className, label }: { className: string; label: string }) {
  return (
    <span className="flex items-center gap-1.5">
      <span className={`h-2.5 w-2.5 rounded border ${className}`} />
      {label}
    </span>
  );
}

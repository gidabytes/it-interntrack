import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";
import type { AttendanceRecord, Page } from "../api/types";
import {
  Badge,
  Empty,
  ErrorNote,
  PageHeader,
  Spinner,
  Table,
} from "../components/ui";
import { useAuth } from "../hooks/useAuth";
import { addDays, businessToday, clock, hhmm, titleCase } from "../lib/format";
import { approvalTone, attendanceTone } from "../lib/status";

export function Attendance() {
  const { can } = useAuth();
  const qc = useQueryClient();
  const today = businessToday();

  const [from, setFrom] = useState(addDays(today, -14));
  const [to, setTo] = useState(today);
  const [approval, setApproval] = useState("");
  const [status, setStatus] = useState("");
  const [note, setNote] = useState<string | null>(null);

  const { data, isLoading, error, refetch } = useQuery({
    queryKey: ["attendance", from, to, approval, status],
    queryFn: () =>
      api.get<Page<AttendanceRecord>>("/api/attendance", {
        date_from: from,
        date_to: to,
        approval_status: approval || undefined,
        status: status || undefined,
        page_size: 300,
      }),
  });

  const approve = useMutation({
    mutationFn: (vars: { id: number; overtime: boolean }) =>
      api.post<AttendanceRecord>(`/api/attendance/${vars.id}/approve`, {
        approve_overtime: vars.overtime,
      }),
    onSuccess: (rec) => {
      setNote(
        `Approved ${rec.business_date}: ${hhmm(rec.creditable_minutes)} credited to OJT hours.`,
      );
      qc.invalidateQueries({ queryKey: ["attendance"] });
      qc.invalidateQueries({ queryKey: ["dashboard"] });
    },
  });

  const canApprove = can("attendance.approve");
  const pendingCount =
    data?.items.filter(
      (r) => r.approval_status === "PENDING" && r.time_in && r.time_out,
    ).length ?? 0;

  return (
    <>
      <PageHeader
        title="Attendance"
        subtitle="Unapproved attendance does not count toward OJT hours."
        actions={
          canApprove && pendingCount > 0 ? (
            <span className="text-xs text-warning">
              {pendingCount} record{pendingCount === 1 ? "" : "s"} awaiting approval
            </span>
          ) : undefined
        }
      />

      <div className="mb-4 flex flex-wrap items-end gap-2">
        <div>
          <label className="label">From</label>
          <input
            type="date"
            className="input w-40"
            value={from}
            onChange={(e) => setFrom(e.target.value)}
          />
        </div>
        <div>
          <label className="label">To</label>
          <input
            type="date"
            className="input w-40"
            value={to}
            onChange={(e) => setTo(e.target.value)}
          />
        </div>
        <div>
          <label className="label">Approval</label>
          <select
            className="input w-36"
            value={approval}
            onChange={(e) => setApproval(e.target.value)}
          >
            <option value="">Any</option>
            <option value="PENDING">Pending</option>
            <option value="APPROVED">Approved</option>
            <option value="REJECTED">Rejected</option>
          </select>
        </div>
        <div>
          <label className="label">Status</label>
          <select
            className="input w-44"
            value={status}
            onChange={(e) => setStatus(e.target.value)}
          >
            <option value="">Any</option>
            {[
              "PRESENT",
              "LATE",
              "UNDERTIME",
              "HALF_DAY",
              "ABSENT",
              "INCOMPLETE_TIME_RECORD",
              "ON_LEAVE",
              "REST_DAY",
              "HOLIDAY",
              "NO_SCHEDULE",
            ].map((s) => (
              <option key={s} value={s}>
                {titleCase(s)}
              </option>
            ))}
          </select>
        </div>
      </div>

      {note ? (
        <p className="mb-4 rounded-lg border border-success/40 bg-success-soft px-4 py-2.5 text-sm text-success">
          {note}
        </p>
      ) : null}
      {approve.error ? (
        <div className="mb-4">
          <ErrorNote error={approve.error} />
        </div>
      ) : null}

      {isLoading ? (
        <Spinner />
      ) : error ? (
        <ErrorNote error={error} onRetry={refetch} />
      ) : !data?.items.length ? (
        <Empty
          title="No attendance records in this range"
          hint="Widen the date range, or check the filters."
        />
      ) : (
        <Table
          head={
            <tr>
              <th className="th">Business date</th>
              <th className="th">Shift</th>
              <th className="th">In</th>
              <th className="th">Out</th>
              <th className="th text-right">Scheduled</th>
              <th className="th text-right">Rendered</th>
              <th className="th text-right">Creditable</th>
              <th className="th text-right">Late</th>
              <th className="th text-right">OT</th>
              <th className="th">Status</th>
              <th className="th">Approval</th>
              {canApprove ? <th className="th" /> : null}
            </tr>
          }
        >
          {data.items.map((r) => {
            const crossesMidnight =
              r.time_in &&
              r.time_out &&
              new Date(r.time_out).toDateString() !==
                new Date(r.time_in).toDateString();
            return (
              <tr key={r.id} className="hover:bg-white/[0.02]">
                <td className="td">
                  {r.business_date}
                  {r.session_sequence > 1 ? (
                    <span className="ml-1.5 text-xs text-slate-500">
                      #{r.session_sequence}
                    </span>
                  ) : null}
                </td>
                <td className="td text-xs text-slate-400">
                  {r.shift_code ?? "—"}
                  {r.is_overnight_shift ? (
                    <span title="Overnight shift" className="ml-1">
                      ⏱
                    </span>
                  ) : null}
                </td>
                <td className="td tnum">{clock(r.time_in)}</td>
                <td className="td tnum">
                  {clock(r.time_out)}
                  {crossesMidnight ? (
                    <span
                      className="ml-1 text-[10px] text-slate-500"
                      title="Timed out the following calendar day"
                    >
                      +1
                    </span>
                  ) : null}
                </td>
                <td className="td tnum text-right text-slate-400">
                  {hhmm(r.scheduled_net_minutes)}
                </td>
                <td className="td tnum text-right">{hhmm(r.actual_net_minutes)}</td>
                <td className="td tnum text-right font-medium">
                  {hhmm(r.creditable_minutes)}
                  {r.credit_capped ? (
                    <span className="ml-1 text-warning" title={r.cap_reason ?? ""}>
                      *
                    </span>
                  ) : null}
                </td>
                <td className="td tnum text-right">
                  {r.late_minutes ? (
                    <span className="text-warning">{r.late_minutes}m</span>
                  ) : (
                    "—"
                  )}
                </td>
                <td className="td tnum text-right">
                  {r.overtime_minutes ? (
                    <span
                      className={
                        r.approved_overtime_minutes ? "text-success" : "text-slate-500"
                      }
                      title={
                        r.approved_overtime_minutes
                          ? `${r.approved_overtime_minutes}m approved`
                          : "Detected but not authorised, so not credited"
                      }
                    >
                      {r.overtime_minutes}m
                    </span>
                  ) : (
                    "—"
                  )}
                </td>
                <td className="td">
                  <Badge className={attendanceTone(r.status)}>
                    {titleCase(r.status)}
                  </Badge>
                </td>
                <td className="td">
                  <Badge className={approvalTone(r.approval_status)}>
                    {titleCase(r.approval_status)}
                  </Badge>
                  {r.is_locked ? (
                    <span
                      className="ml-1.5 text-xs text-slate-500"
                      title="Locked by a signed timesheet"
                    >
                      🔒
                    </span>
                  ) : null}
                </td>
                {canApprove ? (
                  <td className="td">
                    {r.approval_status === "PENDING" && r.time_in && r.time_out ? (
                      <div className="flex gap-1.5">
                        <button
                          className="btn-ghost px-2 py-1 text-xs"
                          disabled={approve.isPending}
                          onClick={() =>
                            approve.mutate({ id: r.id, overtime: false })
                          }
                        >
                          Approve
                        </button>
                        {r.overtime_minutes > 0 ? (
                          <button
                            className="btn-ghost px-2 py-1 text-xs text-success"
                            disabled={approve.isPending}
                            title={`Approve with ${r.overtime_minutes}m overtime`}
                            onClick={() =>
                              approve.mutate({ id: r.id, overtime: true })
                            }
                          >
                            + OT
                          </button>
                        ) : null}
                      </div>
                    ) : null}
                  </td>
                ) : null}
              </tr>
            );
          })}
        </Table>
      )}

      <p className="mt-3 text-xs text-slate-500">
        ⏱ overnight shift · +1 timed out the next calendar day · * daily credit
        ceiling applied · 🔒 locked by a signed timesheet
      </p>
    </>
  );
}

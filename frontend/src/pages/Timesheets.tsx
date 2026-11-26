import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";
import type { Timesheet } from "../api/types";
import {
  Badge,
  Empty,
  ErrorNote,
  PageHeader,
  Spinner,
  Table,
} from "../components/ui";
import { useAuth } from "../hooks/useAuth";
import { dateOnly, hhmm, hours } from "../lib/format";
import { approvalTone } from "../lib/status";

export function Timesheets() {
  const { can, hasRole } = useAuth();
  const qc = useQueryClient();
  const [note, setNote] = useState<string | null>(null);

  const { data, isLoading, error, refetch } = useQuery({
    queryKey: ["timesheets"],
    queryFn: () => api.get<Timesheet[]>("/api/timesheets"),
  });

  const invalidate = () => {
    qc.invalidateQueries({ queryKey: ["timesheets"] });
  };

  const submit = useMutation({
    mutationFn: (id: number) => api.post<Timesheet>(`/api/timesheets/${id}/submit`),
    onSuccess: (ts) => {
      setNote(`${ts.reference_no} submitted for signature.`);
      invalidate();
    },
  });

  const sign = useMutation({
    mutationFn: (id: number) =>
      api.post<Timesheet>(`/api/timesheets/${id}/sign`, {
        remarks: "Reviewed and verified.",
      }),
    onSuccess: (ts) => {
      setNote(
        `${ts.reference_no} signed by ${ts.signed_by_name}. The underlying attendance records are now locked.`,
      );
      invalidate();
    },
  });

  const verify = useMutation({
    mutationFn: (id: number) =>
      api.get<{ valid: boolean | null; message: string }>(
        `/api/timesheets/${id}/verify`,
      ),
    onSuccess: (r) => setNote(r.message),
  });

  const canSign = can("timesheet.sign");
  const isIntern = hasRole("INTERN");

  return (
    <>
      <PageHeader
        title={isIntern ? "My Timesheets" : "Timesheets"}
        subtitle="Signing freezes the figures and locks the attendance behind them. Later corrections create a new revision rather than altering what was signed."
      />

      {note ? (
        <p className="mb-4 rounded-ios border border-primary/40 bg-primary-soft px-4 py-2.5 text-sm text-label">
          {note}
        </p>
      ) : null}
      {[submit.error, sign.error, verify.error].filter(Boolean).map((e, i) => (
        <div key={i} className="mb-4">
          <ErrorNote error={e} />
        </div>
      ))}

      {isLoading ? (
        <Spinner />
      ) : error ? (
        <ErrorNote error={error} onRetry={refetch} />
      ) : !data?.length ? (
        <Empty
          title="No timesheets yet"
          hint="A supervisor generates these per month from the Interns page."
        />
      ) : (
        <Table
          head={
            <tr>
              <th className="th">Reference</th>
              <th className="th">Period</th>
              <th className="th text-right">Rev</th>
              <th className="th text-right">Days</th>
              <th className="th text-right">Creditable</th>
              <th className="th text-right">Cumulative</th>
              <th className="th text-right">Remaining</th>
              <th className="th">Status</th>
              <th className="th">Signed by</th>
              <th className="th" />
            </tr>
          }
        >
          {data.map((t) => (
            <tr key={t.id} className="hover:bg-fill">
              <td className="td font-mono text-xs text-label-2">
                {t.reference_no}
              </td>
              <td className="td">
                {dateOnly(t.period_start)} – {dateOnly(t.period_end)}
              </td>
              <td className="td tnum text-right text-label-2">{t.revision}</td>
              <td className="td tnum text-right">
                {t.total_days_present}
                <span className="text-label-3">/{t.total_days_scheduled}</span>
                {t.total_days_absent ? (
                  <span className="ml-1 text-xs text-danger">
                    −{t.total_days_absent}
                  </span>
                ) : null}
              </td>
              <td className="td tnum text-right font-medium">
                {hhmm(t.total_creditable_minutes)}
              </td>
              <td className="td tnum text-right text-label-2">
                {hours(t.cumulative_approved_minutes, 1)} h
              </td>
              <td className="td tnum text-right">
                {hours(t.remaining_minutes, 1)} h
              </td>
              <td className="td">
                <Badge className={approvalTone(t.status)}>{t.status}</Badge>
                {t.is_locked ? (
                  <span className="ml-1.5 text-xs text-label-3" title="Locked">
                    🔒
                  </span>
                ) : null}
              </td>
              <td className="td text-xs text-label-2">
                {t.signed_by_name ? (
                  <>
                    {t.signed_by_name}
                    <br />
                    <span className="font-mono text-[10px] text-label-3">
                      {t.signature_hash?.slice(0, 12)}…
                    </span>
                  </>
                ) : (
                  "—"
                )}
              </td>
              <td className="td">
                <div className="flex gap-1.5">
                  {t.status === "DRAFT" || t.status === "REJECTED" ? (
                    <button
                      className="btn-ghost px-2 py-1 text-xs"
                      disabled={submit.isPending}
                      onClick={() => submit.mutate(t.id)}
                    >
                      Submit
                    </button>
                  ) : null}
                  {canSign && t.status === "SUBMITTED" ? (
                    <button
                      className="btn-primary px-2 py-1 text-xs"
                      disabled={sign.isPending}
                      onClick={() => sign.mutate(t.id)}
                    >
                      Sign
                    </button>
                  ) : null}
                  {t.status === "SIGNED" ? (
                    <button
                      className="btn-ghost px-2 py-1 text-xs"
                      disabled={verify.isPending}
                      onClick={() => verify.mutate(t.id)}
                    >
                      Verify
                    </button>
                  ) : null}
                </div>
              </td>
            </tr>
          ))}
        </Table>
      )}

      {canSign ? (
        <p className="mt-3 text-xs text-label-3">
          Signing is refused while any punched record in the period is still
          unapproved — approve those first. That is protecting your signature,
          not obstructing it.
        </p>
      ) : null}
    </>
  );
}

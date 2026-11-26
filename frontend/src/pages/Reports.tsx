import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api, ApiError } from "../api/client";
import type { ReportCatalogue } from "../api/types";
import { ErrorNote, PageHeader, Spinner } from "../components/ui";
import { useAuth } from "../hooks/useAuth";
import { addDays, businessToday } from "../lib/format";

type Fmt = "xlsx" | "csv" | "pdf" | "json";

export function Reports() {
  const { can } = useAuth();
  const today = businessToday();

  const [preset, setPreset] = useState("THIS_MONTH");
  const [from, setFrom] = useState(addDays(today, -30));
  const [to, setTo] = useState(today);
  const [busy, setBusy] = useState<string | null>(null);
  const [err, setErr] = useState<unknown>(null);
  const [preview, setPreview] = useState<{
    title: string;
    columns: string[];
    rows: unknown[][];
    row_count: number;
  } | null>(null);

  const { data, isLoading, error, refetch } = useQuery({
    queryKey: ["report-catalogue"],
    queryFn: () => api.get<ReportCatalogue>("/api/reports"),
  });

  const canExport = can("report.export");
  const custom = preset === "CUSTOM";

  function params() {
    return custom
      ? { preset: "CUSTOM", date_from: from, date_to: to }
      : { preset };
  }

  async function run(key: string, fmt: Fmt) {
    setErr(null);
    setBusy(`${key}:${fmt}`);
    try {
      if (fmt === "json") {
        const res = await api.get<{
          title: string;
          columns: string[];
          rows: unknown[][];
          row_count: number;
        }>(`/api/reports/${key}`, { ...params(), fmt: "json" });
        setPreview(res);
      } else {
        await api.download(`/api/reports/${key}`, { ...params(), fmt }, `${key}.${fmt}`);
      }
    } catch (e) {
      setErr(e);
      if (e instanceof ApiError && e.status === 400) {
        // individual_timesheet needs an intern_id; surfaced by the message.
      }
    } finally {
      setBusy(null);
    }
  }

  if (isLoading) return <Spinner />;
  if (error) return <ErrorNote error={error} onRetry={refetch} />;

  return (
    <>
      <PageHeader
        title="Reports"
        subtitle="Every report exports to Excel, CSV and PDF with print-friendly layouts and signature blocks."
      />

      <div className="card mb-5">
        <div className="flex flex-wrap items-end gap-3">
          <div>
            <label className="label">Period</label>
            <select
              className="input w-44"
              value={preset}
              onChange={(e) => setPreset(e.target.value)}
            >
              {(data?.presets ?? []).map((p) => (
                <option key={p} value={p}>
                  {p.replace(/_/g, " ").toLowerCase()}
                </option>
              ))}
            </select>
          </div>
          {custom ? (
            <>
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
            </>
          ) : null}
        </div>
        {!canExport ? (
          <p className="mt-3 text-xs text-warning">
            Your role can view reports but not export them; only Preview is
            available.
          </p>
        ) : null}
      </div>

      {err ? (
        <div className="mb-5">
          <ErrorNote error={err} />
        </div>
      ) : null}

      <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
        {(data?.reports ?? []).map((r) => (
          <div key={r.key} className="card flex flex-col justify-between">
            <div>
              <h3 className="text-sm font-medium text-label">{r.title}</h3>
              <p className="mt-1 font-mono text-[11px] text-label-3">{r.key}</p>
              {r.key === "individual_timesheet" ? (
                <p className="mt-2 text-xs text-warning">
                  Needs an intern — open one from the Interns page.
                </p>
              ) : null}
            </div>
            <div className="mt-4 flex flex-wrap gap-1.5">
              <button
                className="btn-ghost px-2 py-1 text-xs"
                disabled={busy === `${r.key}:json`}
                onClick={() => run(r.key, "json")}
              >
                Preview
              </button>
              {(["xlsx", "csv", "pdf"] as Fmt[]).map((f) => (
                <button
                  key={f}
                  className="btn-ghost px-2 py-1 text-xs uppercase"
                  disabled={!canExport || busy === `${r.key}:${f}`}
                  onClick={() => run(r.key, f)}
                >
                  {busy === `${r.key}:${f}` ? "…" : f}
                </button>
              ))}
            </div>
          </div>
        ))}
      </div>

      {preview ? (
        <section className="mt-6 card">
          <div className="mb-3 flex items-baseline justify-between gap-4">
            <div>
              <h2 className="text-sm font-semibold text-label">
                {preview.title}
              </h2>
              <p className="text-xs text-label-3">
                {preview.row_count} row{preview.row_count === 1 ? "" : "s"}
                {preview.row_count > 50 ? " · showing the first 50" : ""}
              </p>
            </div>
            <button
              className="btn-ghost px-2 py-1 text-xs"
              onClick={() => setPreview(null)}
            >
              Close
            </button>
          </div>
          <div className="overflow-x-auto rounded-ios border border-separator">
            <table className="min-w-full">
              <thead className="bg-fill">
                <tr>
                  {preview.columns.map((c) => (
                    <th key={c} className="th">
                      {c}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {preview.rows.slice(0, 50).map((row, i) => (
                  <tr key={i}>
                    {row.map((cell, j) => (
                      <td key={j} className="td text-label-2">
                        {cell === null || cell === "" ? "—" : String(cell)}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      ) : null}
    </>
  );
}

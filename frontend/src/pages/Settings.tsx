import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";
import { ErrorNote, PageHeader, Spinner } from "../components/ui";

interface Setting {
  key: string;
  value: string;
  value_type: string;
  description: string;
  is_editable: boolean;
  updated_at: string | null;
}

interface SettingsResponse {
  categories: string[];
  settings: Record<string, Setting[]>;
}

interface StatusCheck {
  check: string;
  ok: boolean;
  detail: string;
}

interface SystemStatus {
  application: string;
  version: string;
  business_timezone: string;
  database: string;
  debug: boolean;
  all_passed: boolean;
  checks: StatusCheck[];
}

export function Settings() {
  const qc = useQueryClient();
  const [edits, setEdits] = useState<Record<string, string>>({});
  const [saved, setSaved] = useState<string | null>(null);

  const statusQ = useQuery({
    queryKey: ["system-status"],
    queryFn: () => api.get<SystemStatus>("/api/admin/system-status"),
  });

  const settingsQ = useQuery({
    queryKey: ["settings"],
    queryFn: () => api.get<SettingsResponse>("/api/admin/settings"),
  });

  const save = useMutation({
    mutationFn: (v: { key: string; value: unknown }) =>
      api.put(`/api/admin/settings/${v.key}`, { value: v.value }),
    onSuccess: (_d, v) => {
      setSaved(`${v.key} updated.`);
      setEdits((e) => {
        const next = { ...e };
        delete next[v.key];
        return next;
      });
      qc.invalidateQueries({ queryKey: ["settings"] });
    },
  });

  function coerce(s: Setting, raw: string): unknown {
    if (s.value_type === "int") return Number.parseInt(raw, 10);
    if (s.value_type === "float") return Number.parseFloat(raw);
    if (s.value_type === "bool") return raw === "true";
    return raw;
  }

  return (
    <>
      <PageHeader
        title="System Settings"
        subtitle="Every computation rule is stored in the database and takes effect without a deploy. Changing a rule affects future computations; it does not rewrite history."
      />

      {statusQ.isLoading ? (
        <section className="card mb-5">
          <Spinner label="Running deployment checks…" />
        </section>
      ) : null}

      {statusQ.data ? (
        <section className="card mb-5">
          <div className="mb-3 flex flex-wrap items-baseline justify-between gap-3">
            <h2 className="text-sm font-semibold text-slate-200">
              Deployment check
            </h2>
            <span className="text-xs text-slate-500">
              {statusQ.data.application} {statusQ.data.version} ·{" "}
              {statusQ.data.database} · {statusQ.data.business_timezone}
            </span>
          </div>
          <ul className="space-y-2">
            {statusQ.data.checks.map((c) => (
              <li key={c.check} className="flex items-start gap-3 text-sm">
                <span
                  className={`mt-0.5 grid h-4 w-4 shrink-0 place-items-center rounded-full text-[10px] font-bold ${
                    c.ok
                      ? "bg-success-soft text-success"
                      : "bg-warning-soft text-warning"
                  }`}
                >
                  {c.ok ? "✓" : "!"}
                </span>
                <div>
                  <p className="text-slate-200">{c.check}</p>
                  <p className="text-xs leading-relaxed text-slate-500">
                    {c.detail}
                  </p>
                </div>
              </li>
            ))}
          </ul>
        </section>
      ) : null}

      {saved ? (
        <p className="mb-4 rounded-lg border border-success/40 bg-success-soft px-4 py-2.5 text-sm text-success">
          {saved}
        </p>
      ) : null}
      {save.error ? (
        <div className="mb-4">
          <ErrorNote error={save.error} />
        </div>
      ) : null}

      {settingsQ.isLoading ? (
        <Spinner />
      ) : settingsQ.error ? (
        <ErrorNote error={settingsQ.error} onRetry={settingsQ.refetch} />
      ) : (
        <div className="space-y-5">
          {(settingsQ.data?.categories ?? []).map((cat) => (
            <section key={cat} className="card">
              <h2 className="mb-4 text-sm font-semibold text-slate-200">
                {cat.replace(/_/g, " ")}
              </h2>
              <div className="space-y-4">
                {(settingsQ.data?.settings[cat] ?? []).map((s) => {
                  const current = edits[s.key] ?? s.value;
                  const dirty = edits[s.key] !== undefined && edits[s.key] !== s.value;
                  return (
                    <div
                      key={s.key}
                      className="grid gap-2 border-b border-edge/60 pb-4 last:border-0 last:pb-0 lg:grid-cols-[1fr_auto]"
                    >
                      <div className="min-w-0">
                        <p className="font-mono text-xs text-slate-300">{s.key}</p>
                        <p className="mt-0.5 text-xs leading-relaxed text-slate-500">
                          {s.description}
                        </p>
                      </div>
                      <div className="flex items-center gap-2">
                        {s.value_type === "bool" ? (
                          <select
                            className="input w-28"
                            value={current}
                            disabled={!s.is_editable}
                            onChange={(e) =>
                              setEdits((x) => ({ ...x, [s.key]: e.target.value }))
                            }
                          >
                            <option value="true">true</option>
                            <option value="false">false</option>
                          </select>
                        ) : (
                          <input
                            className="input w-28 tnum"
                            value={current}
                            disabled={!s.is_editable}
                            onChange={(e) =>
                              setEdits((x) => ({ ...x, [s.key]: e.target.value }))
                            }
                          />
                        )}
                        <button
                          className="btn-primary px-2.5 py-1.5 text-xs"
                          disabled={!dirty || save.isPending}
                          onClick={() =>
                            save.mutate({ key: s.key, value: coerce(s, current) })
                          }
                        >
                          Save
                        </button>
                      </div>
                    </div>
                  );
                })}
              </div>
            </section>
          ))}
        </div>
      )}
    </>
  );
}

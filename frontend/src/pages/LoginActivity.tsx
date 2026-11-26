import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "../api/client";
import type { AuditEntry } from "../api/types";
import { Badge, Empty, ErrorNote, KpiCard, PageHeader, Spinner, Table } from "../components/ui";

/**
 * Login tracker.
 *
 * Reads the audit trail rather than keeping a second log of its own, so what
 * is shown here is the same immutable record an auditor would be given.
 *
 * Failed attempts are listed alongside successful ones deliberately. A single
 * failure is somebody fat-fingering a password; a run of them across several
 * accounts from one address is the thing worth noticing, and that pattern is
 * only visible when both outcomes appear on the same timeline.
 */

const SIGN_IN_ACTIONS =
  "LOGIN,LOGIN_FAILED,LOGIN_LOCKED,PASSWORD_CHANGED,PASSWORD_RESET";

const ACTION_TONE: Record<string, string> = {
  LOGIN: "border-success/30 bg-success-soft text-success",
  LOGIN_FAILED: "border-warning/30 bg-warning-soft text-warning",
  LOGIN_LOCKED: "border-danger/30 bg-danger-soft text-danger",
  PASSWORD_CHANGED: "border-primary/30 bg-primary-soft text-primary",
  PASSWORD_RESET: "border-indigo/30 bg-indigo-soft text-indigo-strong",
};

const ACTION_LABEL: Record<string, string> = {
  LOGIN: "Signed in",
  LOGIN_FAILED: "Failed",
  LOGIN_LOCKED: "Locked out",
  PASSWORD_CHANGED: "Password changed",
  PASSWORD_RESET: "Password reset by admin",
};

type Filter = "all" | "LOGIN" | "LOGIN_FAILED" | "LOGIN_LOCKED";

const FILTERS: { value: Filter; label: string }[] = [
  { value: "all", label: "All" },
  { value: "LOGIN", label: "Successful" },
  { value: "LOGIN_FAILED", label: "Failed" },
  { value: "LOGIN_LOCKED", label: "Lockouts" },
];

function daysAgo(n: number): string {
  const d = new Date();
  d.setDate(d.getDate() - n);
  return d.toISOString().slice(0, 10);
}

/** Enough of the user agent to tell a browser from a script, no more. */
function shortAgent(agent: string | null): string {
  if (!agent) return "—";
  const m =
    /(Edg|OPR|Chrome|Firefox|Safari|curl|python-requests|PostmanRuntime)\/?([\d.]+)?/.exec(
      agent,
    );
  if (!m) return agent.slice(0, 28);
  const name = m[1] === "Edg" ? "Edge" : m[1] === "OPR" ? "Opera" : m[1];
  const platform = /Windows/.test(agent)
    ? " · Windows"
    : /Android/.test(agent)
      ? " · Android"
      : /iPhone|iPad/.test(agent)
        ? " · iOS"
        : /Mac OS X/.test(agent)
          ? " · macOS"
          : /Linux/.test(agent)
            ? " · Linux"
            : "";
  return `${name}${platform}`;
}

export function LoginActivity() {
  const [filter, setFilter] = useState<Filter>("all");
  const [sinceDays, setSinceDays] = useState(7);
  const [who, setWho] = useState("");

  const query = useQuery({
    queryKey: ["login-activity", filter, sinceDays, who],
    queryFn: () =>
      api.get<AuditEntry[]>("/api/admin/audit", {
        action: filter === "all" ? SIGN_IN_ACTIONS : filter,
        date_from: daysAgo(sinceDays),
        q: who.trim() || undefined,
        limit: 500,
      }),
  });

  const rows = query.data ?? [];

  const stats = useMemo(() => {
    const ok = rows.filter((r) => r.action === "LOGIN");
    const failed = rows.filter((r) => r.action === "LOGIN_FAILED");
    const locked = rows.filter((r) => r.action === "LOGIN_LOCKED");
    // Addresses that produced failures but never a success are the ones worth
    // a second look; a mix of both is usually somebody mistyping.
    const okIps = new Set(ok.map((r) => r.ip_address).filter(Boolean));
    const suspect = new Set(
      failed
        .map((r) => r.ip_address)
        .filter((ip): ip is string => Boolean(ip) && !okIps.has(ip)),
    );
    return {
      ok: ok.length,
      failed: failed.length,
      locked: locked.length,
      people: new Set(ok.map((r) => r.actor).filter(Boolean)).size,
      suspect: [...suspect],
    };
  }, [rows]);

  return (
    <div>
      <PageHeader
        title="Login Activity"
        subtitle={`Sign-in record for the last ${sinceDays} days, taken from the audit trail`}
        actions={
          <select
            className="input w-auto"
            value={sinceDays}
            onChange={(e) => setSinceDays(Number(e.target.value))}
            aria-label="Period"
          >
            <option value={1}>Last 24 hours</option>
            <option value={7}>Last 7 days</option>
            <option value={30}>Last 30 days</option>
            <option value={90}>Last 90 days</option>
          </select>
        }
      />

      <div className="mb-5 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <KpiCard label="Successful sign-ins" value={stats.ok} tone="ok" />
        <KpiCard
          label="Failed attempts"
          value={stats.failed}
          tone={stats.failed > 0 ? "warn" : "default"}
        />
        <KpiCard
          label="Lockouts"
          value={stats.locked}
          tone={stats.locked > 0 ? "bad" : "default"}
          sub="After 5 failures, for 15 minutes"
        />
        <KpiCard label="Distinct accounts" value={stats.people} />
      </div>

      {stats.suspect.length ? (
        <div className="mb-5 rounded-ios-md border border-warning/30 bg-warning-soft p-4">
          <p className="text-ios-subhead font-medium text-warning">
            {stats.suspect.length} address
            {stats.suspect.length === 1 ? "" : "es"} produced failed attempts
            and never signed in successfully
          </p>
          <p className="mt-1 text-ios-footnote text-label-2">
            Worth a look. A legitimate user who mistypes a password normally
            gets in on a later attempt from the same address.
          </p>
          <p className="tnum mt-2 text-ios-footnote text-label">
            {stats.suspect.slice(0, 8).join(", ")}
            {stats.suspect.length > 8 ? ", …" : ""}
          </p>
        </div>
      ) : null}

      <div className="mb-4 flex flex-wrap items-center gap-3">
        <div className="segmented">
          {FILTERS.map((f) => (
            <button
              key={f.value}
              className={`segmented-item ${
                filter === f.value ? "segmented-item-active" : ""
              }`}
              onClick={() => setFilter(f.value)}
              aria-pressed={filter === f.value}
            >
              {f.label}
            </button>
          ))}
        </div>
        <input
          className="input w-auto min-w-[14rem] flex-1 sm:flex-none"
          value={who}
          onChange={(e) => setWho(e.target.value)}
          placeholder="Filter by username…"
          aria-label="Filter by username"
        />
      </div>

      {query.isLoading ? <Spinner /> : null}
      {query.isError ? (
        <ErrorNote error={query.error} onRetry={() => query.refetch()} />
      ) : null}

      {query.isSuccess && !rows.length ? (
        <Empty
          title="No sign-in activity in this period"
          hint="Widen the period, or clear the username filter."
        />
      ) : null}

      {rows.length ? (
        <Table
          head={
            <tr>
              <th className="th">When</th>
              <th className="th">Account</th>
              <th className="th">Outcome</th>
              <th className="th">Roles</th>
              <th className="th">IP address</th>
              <th className="th">Client</th>
            </tr>
          }
        >
          {rows.map((r) => (
            <tr key={r.id} className="hover:bg-fill">
              <td className="td tnum text-label-2">
                {new Date(r.created_at).toLocaleString()}
              </td>
              <td className="td font-medium">{r.actor ?? "—"}</td>
              <td className="td">
                <Badge className={ACTION_TONE[r.action] ?? "border-separator bg-fill text-label-2"}>
                  {ACTION_LABEL[r.action] ?? r.action}
                </Badge>
              </td>
              <td className="td text-ios-footnote text-label-2">
                {r.actor_roles?.replaceAll(",", ", ") ?? "—"}
              </td>
              <td className="td tnum">{r.ip_address ?? "—"}</td>
              <td
                className="td text-label-2"
                title={r.user_agent ?? undefined}
              >
                {shortAgent(r.user_agent)}
              </td>
            </tr>
          ))}
        </Table>
      ) : null}

      <p className="mt-4 text-ios-footnote text-label-3">
        Entries come from the append-only audit trail and cannot be edited or
        removed from this screen. The full trail, including record changes and
        approvals, is under Settings.
      </p>
    </div>
  );
}

import type { ReactNode } from "react";

export function Badge({
  children,
  className = "",
}: {
  children: ReactNode;
  className?: string;
}) {
  return (
    <span
      className={`inline-flex items-center rounded-md border px-2 py-0.5 text-xs
                  font-medium ${className}`}
    >
      {children}
    </span>
  );
}

export function Spinner({ label }: { label?: string }) {
  return (
    <div className="flex items-center gap-3 text-sm text-slate-400">
      <span
        className="h-4 w-4 animate-spin rounded-full border-2 border-slate-600
                   border-t-primary"
        aria-hidden
      />
      {label ?? "Loading…"}
    </div>
  );
}

export function ErrorNote({
  error,
  onRetry,
}: {
  error: unknown;
  onRetry?: () => void;
}) {
  const message =
    error instanceof Error ? error.message : "Something went wrong.";
  const problems =
    error && typeof error === "object" && "problems" in error
      ? (error as { problems?: { field: string; message: string }[] }).problems
      : undefined;

  return (
    <div className="rounded-lg border border-danger/40 bg-danger-soft p-4 text-sm">
      <p className="font-medium text-danger">{message}</p>
      {problems?.length ? (
        <ul className="mt-2 space-y-1 text-slate-300">
          {problems.map((p, i) => (
            <li key={i}>
              <span className="text-slate-400">{p.field}:</span> {p.message}
            </li>
          ))}
        </ul>
      ) : null}
      {onRetry ? (
        <button className="btn-ghost mt-3" onClick={onRetry}>
          Try again
        </button>
      ) : null}
    </div>
  );
}

export function Empty({
  title,
  hint,
}: {
  title: string;
  hint?: string;
}) {
  return (
    <div className="rounded-lg border border-dashed border-edge p-8 text-center">
      <p className="text-sm font-medium text-slate-300">{title}</p>
      {hint ? <p className="mt-1 text-xs text-slate-500">{hint}</p> : null}
    </div>
  );
}

export function PageHeader({
  title,
  subtitle,
  actions,
}: {
  title: string;
  subtitle?: string;
  actions?: ReactNode;
}) {
  return (
    <div className="mb-6 flex flex-wrap items-start justify-between gap-4">
      <div>
        <h1 className="text-xl font-semibold text-slate-100">{title}</h1>
        {subtitle ? (
          <p className="mt-1 text-sm text-slate-400">{subtitle}</p>
        ) : null}
      </div>
      {actions ? <div className="flex items-center gap-2">{actions}</div> : null}
    </div>
  );
}

export function KpiCard({
  label,
  value,
  sub,
  tone = "default",
}: {
  label: string;
  value: string | number;
  sub?: string;
  tone?: "default" | "ok" | "warn" | "bad";
}) {
  const accent = {
    default: "text-slate-100",
    ok: "text-success",
    warn: "text-warning",
    bad: "text-danger",
  }[tone];

  return (
    <div className="card">
      <p className="text-xs font-medium uppercase tracking-wide text-slate-400">
        {label}
      </p>
      <p className={`tnum mt-2 text-2xl font-semibold ${accent}`}>{value}</p>
      {sub ? <p className="mt-1 text-xs text-slate-500">{sub}</p> : null}
    </div>
  );
}

export function ProgressBar({
  value,
  tone = "primary",
}: {
  value: number;
  tone?: "primary" | "ok" | "warn" | "bad";
}) {
  const bg = {
    primary: "bg-primary",
    ok: "bg-success",
    warn: "bg-warning",
    bad: "bg-danger",
  }[tone];
  return (
    <div className="h-2 w-full overflow-hidden rounded-full bg-white/10">
      <div
        className={`h-full rounded-full ${bg} transition-all`}
        style={{ width: `${Math.max(0, Math.min(100, value))}%` }}
      />
    </div>
  );
}

export function Table({
  head,
  children,
}: {
  head: ReactNode;
  children: ReactNode;
}) {
  return (
    <div className="overflow-x-auto rounded-xl border border-edge">
      <table className="min-w-full">
        <thead className="bg-white/[0.03]">{head}</thead>
        <tbody>{children}</tbody>
      </table>
    </div>
  );
}

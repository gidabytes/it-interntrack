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
      className={`inline-flex items-center rounded-full border px-2.5 py-0.5
                  text-ios-caption font-medium ${className}`}
    >
      {children}
    </span>
  );
}

export function Spinner({ label }: { label?: string }) {
  return (
    <div className="flex items-center gap-2.5 text-ios-subhead text-label-2">
      <span
        className="h-4 w-4 animate-spin rounded-full border-[1.5px] border-separator
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
    <div className="rounded-ios-md border border-danger/30 bg-danger-soft p-4
                    text-ios-subhead">
      <p className="font-medium text-danger">{message}</p>
      {problems?.length ? (
        <ul className="mt-2 space-y-1 text-label-2">
          {problems.map((p, i) => (
            <li key={i}>
              <span className="text-label-2">{p.field}:</span> {p.message}
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
    <div className="rounded-ios-md border border-separator bg-elevated p-10
                    text-center shadow-ios">
      <p className="text-ios-subhead font-medium text-label">{title}</p>
      {hint ? (
        <p className="mx-auto mt-1.5 max-w-sm text-ios-footnote text-label-3">
          {hint}
        </p>
      ) : null}
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
        <h1 className="text-ios-title1 font-semibold text-label">{title}</h1>
        {subtitle ? (
          <p className="mt-1 text-ios-subhead text-label-2">{subtitle}</p>
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
    default: "text-label",
    ok: "text-success",
    warn: "text-warning",
    bad: "text-danger",
  }[tone];

  return (
    <div className="card">
      <p className={`tnum text-ios-title1 font-semibold leading-none ${accent}`}>
        {value}
      </p>
      <p className="mt-2 text-ios-footnote font-medium text-label-2">{label}</p>
      {sub ? (
        <p className="mt-0.5 text-ios-caption text-label-3">{sub}</p>
      ) : null}
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
    <div className="h-1.5 w-full overflow-hidden rounded-full bg-fill-2">
      <div
        className={`h-full rounded-full ${bg} transition-all duration-500 ease-ios`}
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
    <div className="overflow-x-auto rounded-ios-md border border-separator
                    bg-elevated shadow-ios">
      <table className="min-w-full">
        <thead className="bg-fill/60">{head}</thead>
        <tbody>{children}</tbody>
      </table>
    </div>
  );
}

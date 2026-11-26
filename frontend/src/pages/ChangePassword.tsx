import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { api, ApiError } from "../api/client";
import { useAuth } from "../hooks/useAuth";

const MIN_LENGTH = 10;

/** Rules shown live, so the requirement is visible before submitting. */
function rules(pw: string, current: string) {
  return [
    { ok: pw.length >= MIN_LENGTH, label: `At least ${MIN_LENGTH} characters` },
    { ok: pw.length <= 72, label: "No more than 72 characters" },
    { ok: pw !== current || pw === "", label: "Different from the current password" },
  ];
}

export function ChangePassword({ forced = false }: { forced?: boolean }) {
  const { user, refresh, signOut } = useAuth();
  const navigate = useNavigate();

  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [confirm, setConfirm] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState(false);

  const checks = rules(next, current);
  const matches = next.length > 0 && next === confirm;
  const valid = checks.every((c) => c.ok) && matches;

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setBusy(true);
    try {
      await api.post("/api/auth/change-password", {
        current_password: current,
        new_password: next,
      });
      await refresh();
      setDone(true);
      setTimeout(() => navigate("/", { replace: true }), 900);
    } catch (err) {
      setError(
        err instanceof ApiError
          ? err.message
          : "Could not reach the server. Is the API running?",
      );
    } finally {
      setBusy(false);
    }
  }

  const body = (
    <form onSubmit={submit} className="card space-y-4">
      {forced ? (
        <div className="rounded-lg border border-warning/40 bg-warning-soft px-3 py-2.5">
          <p className="text-sm font-medium text-warning">
            Choose a new password to continue
          </p>
          <p className="mt-1 text-xs text-slate-300">
            This account is using a temporary password. Nothing else is
            available until it is changed.
          </p>
        </div>
      ) : null}

      <div>
        <label className="label" htmlFor="current">
          Current password
        </label>
        <input
          id="current"
          type="password"
          className="input"
          autoComplete="current-password"
          autoFocus
          value={current}
          onChange={(e) => setCurrent(e.target.value)}
          required
        />
      </div>

      <div>
        <label className="label" htmlFor="next">
          New password
        </label>
        <input
          id="next"
          type="password"
          className="input"
          autoComplete="new-password"
          value={next}
          onChange={(e) => setNext(e.target.value)}
          required
        />
        <ul className="mt-2 space-y-1">
          {checks.map((c) => (
            <li
              key={c.label}
              className={`flex items-center gap-2 text-xs ${
                next.length === 0
                  ? "text-slate-500"
                  : c.ok
                    ? "text-success"
                    : "text-slate-400"
              }`}
            >
              <span className="w-3 text-center">{c.ok && next ? "✓" : "·"}</span>
              {c.label}
            </li>
          ))}
        </ul>
      </div>

      <div>
        <label className="label" htmlFor="confirm">
          Confirm new password
        </label>
        <input
          id="confirm"
          type="password"
          className="input"
          autoComplete="new-password"
          value={confirm}
          onChange={(e) => setConfirm(e.target.value)}
          required
        />
        {confirm.length > 0 && !matches ? (
          <p className="mt-1.5 text-xs text-danger">
            The two entries do not match.
          </p>
        ) : null}
      </div>

      {error ? (
        <p className="rounded-lg border border-danger/40 bg-danger-soft px-3 py-2 text-sm text-danger">
          {error}
        </p>
      ) : null}

      {done ? (
        <p className="rounded-lg border border-success/40 bg-success-soft px-3 py-2 text-sm text-success">
          Password changed. Taking you in…
        </p>
      ) : null}

      <button className="btn-primary w-full" disabled={!valid || busy || done}>
        {busy ? "Changing…" : "Change password"}
      </button>

      {forced ? (
        <button
          type="button"
          className="btn-ghost w-full"
          onClick={() => {
            signOut();
            navigate("/login", { replace: true });
          }}
        >
          Sign out instead
        </button>
      ) : null}
    </form>
  );

  // When forced, this replaces the whole application rather than sitting
  // inside the shell: the point is that nothing else is reachable.
  if (forced) {
    return (
      <div className="grid min-h-screen place-items-center px-4">
        <div className="w-full max-w-sm">
          <div className="mb-6 text-center">
            <span className="mx-auto grid h-12 w-12 place-items-center rounded-xl bg-primary text-lg font-bold text-white">
              IT
            </span>
            <h1 className="mt-4 text-lg font-semibold text-slate-100">
              {user?.full_name}
            </h1>
            <p className="mt-1 text-sm text-slate-400">{user?.username}</p>
          </div>
          {body}
        </div>
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-sm">
      <h1 className="mb-1 text-xl font-semibold text-slate-100">
        Change password
      </h1>
      <p className="mb-6 text-sm text-slate-400">
        You will stay signed in on this device.
      </p>
      {body}
    </div>
  );
}

import { useState } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";
import type {
  Page as Paged,
  Registration,
  RegistrationApproved,
  School,
} from "../api/types";
import {
  Badge, Empty, ErrorNote, PageHeader, Spinner,
} from "../components/ui";

/**
 * Review queue for intern self-registrations.
 *
 * Approving is the only path from a public submission to a live intern
 * record, so the screen shows what the applicant actually typed, flags a
 * duplicate address before it can become a second ledger, and makes the
 * reviewer resolve a school that is not yet on file rather than guessing one.
 */

const STATUS_TONE: Record<string, string> = {
  PENDING: "border-warning/30 bg-warning-soft text-warning",
  APPROVED: "border-success/30 bg-success-soft text-success",
  REJECTED: "border-danger/30 bg-danger-soft text-danger",
  WITHDRAWN: "border-separator bg-fill text-label-2",
};

const TABS = ["PENDING", "APPROVED", "REJECTED"] as const;

export function Registrations() {
  const [tab, setTab] = useState<(typeof TABS)[number]>("PENDING");
  const [open, setOpen] = useState<number | null>(null);
  const [approved, setApproved] = useState<RegistrationApproved | null>(null);

  const query = useQuery({
    queryKey: ["registrations", tab],
    queryFn: () =>
      api.get<Paged<Registration>>("/api/registrations", { status: tab }),
  });

  const rows = query.data?.items ?? [];
  const origin = window.location.origin;

  return (
    <div>
      <PageHeader
        title="Registrations"
        subtitle="Interns who registered themselves and are waiting for review"
      />

      <div className="card mb-5">
        <h2 className="label-section">Public registration link</h2>
        <p className="mb-2 text-ios-footnote text-label-2">
          Give this to incoming OJT students. It needs no account, and nothing
          submitted through it becomes live until approved here.
        </p>
        <div className="flex flex-wrap items-center gap-2">
          <code className="flex-1 select-all truncate rounded-ios border border-separator bg-canvas px-3 py-2 text-ios-footnote">
            {origin}/register
          </code>
          <button
            className="btn-tinted btn-sm"
            onClick={() =>
              navigator.clipboard?.writeText(`${origin}/register`)
            }
          >
            Copy
          </button>
          <a
            className="btn-ghost btn-sm"
            href="/register"
            target="_blank"
            rel="noreferrer"
          >
            Open
          </a>
        </div>
        <p className="hint">
          Registration can be closed between intakes under Settings
          (registration_open).
        </p>
      </div>

      {approved ? (
        <ApprovedNotice
          result={approved}
          onDismiss={() => setApproved(null)}
        />
      ) : null}

      <div className="segmented mb-4">
        {TABS.map((t) => (
          <button
            key={t}
            className={`segmented-item ${tab === t ? "segmented-item-active" : ""}`}
            onClick={() => setTab(t)}
            aria-pressed={tab === t}
          >
            {t.charAt(0) + t.slice(1).toLowerCase()}
          </button>
        ))}
      </div>

      {query.isLoading ? <Spinner /> : null}
      {query.isError ? (
        <ErrorNote error={query.error} onRetry={() => query.refetch()} />
      ) : null}

      {query.isSuccess && !rows.length ? (
        <Empty
          title={
            tab === "PENDING"
              ? "Nothing waiting for review"
              : `No ${tab.toLowerCase()} registrations`
          }
          hint={
            tab === "PENDING"
              ? "Submissions through the public link appear here."
              : undefined
          }
        />
      ) : null}

      <div className="space-y-3">
        {rows.map((r) => (
          <RegistrationCard
            key={r.id}
            reg={r}
            expanded={open === r.id}
            onToggle={() => setOpen(open === r.id ? null : r.id)}
            onApproved={(result) => {
              setApproved(result);
              setOpen(null);
            }}
          />
        ))}
      </div>
    </div>
  );
}

function ApprovedNotice({
  result,
  onDismiss,
}: {
  result: RegistrationApproved;
  onDismiss: () => void;
}) {
  return (
    <div className="mb-5 rounded-ios-md border border-success/30 bg-success-soft p-4">
      <p className="text-ios-subhead font-medium text-success">
        {result.message}
      </p>
      {result.temporary_password ? (
        <div className="mt-3 rounded-ios border border-separator bg-elevated p-3">
          <p className="label-section">Sign-in details — shown once</p>
          <dl className="space-y-1 text-ios-footnote">
            <div className="flex gap-2">
              <dt className="w-28 text-label-2">Username</dt>
              <dd className="select-all font-medium">{result.username}</dd>
            </div>
            <div className="flex gap-2">
              <dt className="w-28 text-label-2">Temporary password</dt>
              <dd className="tnum select-all font-medium">
                {result.temporary_password}
              </dd>
            </div>
          </dl>
          <p className="hint">
            Not stored in readable form and not recoverable. Send it to the
            intern now; they must change it at first sign-in.
          </p>
        </div>
      ) : null}
      <div className="mt-3 flex gap-2">
        <Link className="btn-tinted btn-sm" to={`/interns/${result.intern_id}`}>
          Open intern record
        </Link>
        <button className="btn-ghost btn-sm" onClick={onDismiss}>
          Dismiss
        </button>
      </div>
    </div>
  );
}

function RegistrationCard({
  reg,
  expanded,
  onToggle,
  onApproved,
}: {
  reg: Registration;
  expanded: boolean;
  onToggle: () => void;
  onApproved: (r: RegistrationApproved) => void;
}) {
  const needsSchool = reg.school_id === null;

  return (
    <article className="card">
      <button
        className="flex w-full items-start justify-between gap-3 text-left"
        onClick={onToggle}
        aria-expanded={expanded}
      >
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <h2 className="text-ios-body font-semibold">
              {reg.applicant_name}
            </h2>
            <Badge className={STATUS_TONE[reg.status] ?? ""}>
              {reg.status}
            </Badge>
            {reg.duplicate_of_intern_id ? (
              <Badge className="border-danger/30 bg-danger-soft text-danger">
                Duplicate address
              </Badge>
            ) : null}
            {needsSchool && reg.status === "PENDING" ? (
              <Badge className="border-warning/30 bg-warning-soft text-warning">
                School not on file
              </Badge>
            ) : null}
          </div>
          <p className="mt-1 truncate text-ios-footnote text-label-2">
            {reg.school_label ?? "No school given"}
            {reg.course ? ` · ${reg.course}` : ""}
            {reg.year_level ? ` · ${reg.year_level}` : ""}
          </p>
          <p className="tnum mt-0.5 text-ios-caption text-label-3">
            {reg.reference_code} · submitted{" "}
            {new Date(reg.created_at).toLocaleDateString()}
          </p>
        </div>
        <span className="shrink-0 text-ios-footnote text-primary">
          {expanded ? "Hide" : "Review"}
        </span>
      </button>

      {expanded ? (
        <div className="mt-4 border-t border-separator pt-4">
          <div className="grid gap-5 lg:grid-cols-2">
            <section>
              <h3 className="label-section">As submitted</h3>
              <dl className="space-y-1.5 text-ios-footnote">
                <Detail label="Email" value={reg.email} />
                <Detail label="Mobile" value={reg.contact_number} />
                <Detail
                  label="School"
                  value={reg.school_label}
                  warn={needsSchool ? "typed, not on file" : undefined}
                />
                <Detail label="Course" value={reg.course} />
                <Detail label="Year level" value={reg.year_level} />
                <Detail
                  label="Required hours"
                  value={reg.required_hours ? `${reg.required_hours} h` : null}
                />
                <Detail label="Preferred start" value={reg.ojt_start_date} />
                <Detail
                  label="School deadline"
                  value={reg.school_submission_deadline}
                />
                <Detail
                  label="Interest"
                  value={reg.primary_technical_function}
                />
              </dl>

              <h3 className="label-section mt-4">
                School's OJT / IT coordinator
              </h3>
              <dl className="space-y-1.5 text-ios-footnote">
                <Detail label="Name" value={reg.coordinator_name} />
                <Detail label="Email" value={reg.coordinator_email} />
                <Detail label="Contact" value={reg.coordinator_contact} />
              </dl>

              <h3 className="label-section mt-4">Emergency contact</h3>
              <dl className="space-y-1.5 text-ios-footnote">
                <Detail label="Name" value={reg.emergency_contact_name} />
                <Detail label="Number" value={reg.emergency_contact_number} />
                <Detail
                  label="Relationship"
                  value={reg.emergency_contact_relation}
                />
              </dl>

              {reg.applicant_notes ? (
                <>
                  <h3 className="label-section mt-4">Their notes</h3>
                  <p className="rounded-ios bg-fill p-3 text-ios-footnote text-label-2">
                    {reg.applicant_notes}
                  </p>
                </>
              ) : null}

              <p className="mt-3 text-ios-caption2 text-label-3">
                Submitted from {reg.submitted_ip ?? "an unknown address"}
              </p>
            </section>

            <section>
              {reg.status === "PENDING" ? (
                <ReviewPanel reg={reg} onApproved={onApproved} />
              ) : (
                <div className="rounded-ios-md border border-separator bg-canvas p-4">
                  <h3 className="label-section">Review</h3>
                  <p className="text-ios-footnote text-label-2">
                    {reg.status === "APPROVED"
                      ? "Approved"
                      : reg.status.charAt(0) +
                        reg.status.slice(1).toLowerCase()}
                    {reg.reviewed_at
                      ? ` on ${new Date(reg.reviewed_at).toLocaleString()}`
                      : ""}
                    .
                  </p>
                  {reg.review_notes ? (
                    <p className="mt-2 rounded-ios bg-fill p-3 text-ios-footnote">
                      {reg.review_notes}
                    </p>
                  ) : null}
                  {reg.created_intern_id ? (
                    <Link
                      className="btn-tinted btn-sm mt-3"
                      to={`/interns/${reg.created_intern_id}`}
                    >
                      Open intern record
                    </Link>
                  ) : null}
                </div>
              )}
            </section>
          </div>
        </div>
      ) : null}
    </article>
  );
}

function Detail({
  label,
  value,
  warn,
}: {
  label: string;
  value: string | null | undefined;
  warn?: string;
}) {
  return (
    <div className="flex gap-2">
      <dt className="w-32 shrink-0 text-label-2">{label}</dt>
      <dd className="min-w-0 break-words">
        {value ? (
          <span className="text-label">{value}</span>
        ) : (
          <span className="text-label-3">Not given</span>
        )}
        {warn ? <span className="text-warning"> ({warn})</span> : null}
      </dd>
    </div>
  );
}

function ReviewPanel({
  reg,
  onApproved,
}: {
  reg: Registration;
  onApproved: (r: RegistrationApproved) => void;
}) {
  const qc = useQueryClient();
  const [schoolId, setSchoolId] = useState<string>(
    reg.school_id ? String(reg.school_id) : "",
  );
  const [requiredHours, setRequiredHours] = useState<string>(
    reg.required_hours ? String(reg.required_hours) : "",
  );
  const [startDate, setStartDate] = useState(reg.ojt_start_date ?? "");
  const [deadline, setDeadline] = useState(
    reg.school_submission_deadline ?? "",
  );
  const [createAccount, setCreateAccount] = useState(true);
  const [notes, setNotes] = useState("");
  const [mode, setMode] = useState<"approve" | "reject">("approve");

  const schools = useQuery({
    queryKey: ["schools", false],
    queryFn: () => api.get<School[]>("/api/schools"),
  });

  const invalidate = () => {
    qc.invalidateQueries({ queryKey: ["registrations"] });
    qc.invalidateQueries({ queryKey: ["interns"] });
    qc.invalidateQueries({ queryKey: ["schools"] });
  };

  const approve = useMutation({
    mutationFn: () =>
      api.post<RegistrationApproved>(
        `/api/registrations/${reg.id}/approve`,
        {
          school_id: schoolId ? Number(schoolId) : null,
          required_hours: requiredHours ? Number(requiredHours) : null,
          ojt_start_date: startDate || null,
          school_submission_deadline: deadline || null,
          create_user_account: createAccount,
          review_notes: notes || null,
        },
      ),
    onSuccess: (result) => {
      invalidate();
      onApproved(result);
    },
  });

  const reject = useMutation({
    mutationFn: () =>
      api.post(`/api/registrations/${reg.id}/reject`, {
        review_notes: notes,
      }),
    onSuccess: invalidate,
  });

  const blocked = Boolean(reg.duplicate_of_intern_id);

  return (
    <div className="rounded-ios-md border border-separator bg-canvas p-4">
      <h3 className="label-section">Review</h3>

      {blocked ? (
        <div className="mb-3 rounded-ios border border-danger/30 bg-danger-soft p-3 text-ios-footnote">
          <p className="font-medium text-danger">
            An intern already uses this email address.
          </p>
          <p className="mt-1 text-label-2">
            Approving would create a second record and split their hours across
            two ledgers. Reject this as a duplicate instead.
          </p>
          <Link
            className="btn-tinted btn-sm mt-2"
            to={`/interns/${reg.duplicate_of_intern_id}`}
          >
            Open the existing record
          </Link>
        </div>
      ) : null}

      <div className="segmented mb-3">
        <button
          className={`segmented-item ${mode === "approve" ? "segmented-item-active" : ""}`}
          onClick={() => setMode("approve")}
          disabled={blocked}
        >
          Approve
        </button>
        <button
          className={`segmented-item ${mode === "reject" ? "segmented-item-active" : ""}`}
          onClick={() => setMode("reject")}
        >
          Reject
        </button>
      </div>

      {approve.isError ? (
        <div className="mb-3">
          <ErrorNote error={approve.error} />
        </div>
      ) : null}
      {reject.isError ? (
        <div className="mb-3">
          <ErrorNote error={reject.error} />
        </div>
      ) : null}

      {mode === "approve" ? (
        <>
          <div className="space-y-3">
            <div>
              <label className="label">
                School<span className="text-danger"> *</span>
              </label>
              <select
                className="input"
                value={schoolId}
                onChange={(e) => setSchoolId(e.target.value)}
              >
                <option value="">Select a school…</option>
                {(schools.data ?? []).map((s) => (
                  <option key={s.id} value={String(s.id)}>
                    {s.name}
                  </option>
                ))}
              </select>
              {reg.school_id === null ? (
                <p className="hint">
                  They typed &ldquo;{reg.school_name_text}&rdquo;. Match it to a
                  school on file, or add it under Schools first.
                </p>
              ) : null}
            </div>

            <div className="grid gap-3 sm:grid-cols-2">
              <div>
                <label className="label">Required hours</label>
                <input
                  className="input tnum"
                  type="number"
                  min={1}
                  value={requiredHours}
                  onChange={(e) => setRequiredHours(e.target.value)}
                  placeholder="From the school default"
                />
              </div>
              <div>
                <label className="label">Start date</label>
                <input
                  className="input"
                  type="date"
                  value={startDate}
                  onChange={(e) => setStartDate(e.target.value)}
                />
              </div>
            </div>

            <div>
              <label className="label">School submission deadline</label>
              <input
                className="input"
                type="date"
                value={deadline}
                onChange={(e) => setDeadline(e.target.value)}
              />
              <p className="hint">Drives the completion forecast.</p>
            </div>

            <label className="flex items-start gap-2 text-ios-footnote">
              <input
                type="checkbox"
                className="mt-0.5 accent-primary"
                checked={createAccount}
                onChange={(e) => setCreateAccount(e.target.checked)}
              />
              <span>
                Create a sign-in account for the time clock
                <span className="block text-label-3">
                  A one-time password is generated and shown once. They must
                  change it at first sign-in.
                </span>
              </span>
            </label>

            <div>
              <label className="label">Note (optional)</label>
              <textarea
                className="input min-h-[60px]"
                value={notes}
                onChange={(e) => setNotes(e.target.value)}
                placeholder="Recorded against the registration."
              />
            </div>
          </div>

          <button
            className="btn-primary mt-4 w-full"
            onClick={() => approve.mutate()}
            disabled={blocked || !schoolId || approve.isPending}
          >
            {approve.isPending
              ? "Creating intern…"
              : "Approve and create intern"}
          </button>
          {!schoolId ? (
            <p className="mt-1 text-center text-ios-caption text-label-3">
              A school is required before approving.
            </p>
          ) : null}
        </>
      ) : (
        <>
          <label className="label">
            Reason<span className="text-danger"> *</span>
          </label>
          <textarea
            className="input min-h-[90px]"
            value={notes}
            onChange={(e) => setNotes(e.target.value)}
            placeholder="Duplicate submission; already registered under REG-2026-…"
          />
          <p className="hint">
            The applicant sees this when they check their reference code, so
            write it for them.
          </p>
          <button
            className="btn-danger mt-3 w-full"
            onClick={() => reject.mutate()}
            disabled={notes.trim().length < 3 || reject.isPending}
          >
            {reject.isPending ? "Rejecting…" : "Reject registration"}
          </button>
        </>
      )}
    </div>
  );
}

import { useMemo, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { api } from "../api/client";
import type {
  PublicSchool,
  RegistrationAck,
  RegistrationInput,
  RegistrationOpen,
  RegistrationStatusOut,
} from "../api/types";
import { Badge, ErrorNote, Spinner } from "../components/ui";

/**
 * Public intern registration.
 *
 * Reachable without signing in, which is the point: an incoming OJT student
 * has no account yet. Nothing submitted here becomes operational until a
 * supervisor approves it, so the page collects and acknowledges, and promises
 * nothing further.
 */
export function Register() {
  const [submitted, setSubmitted] = useState<RegistrationAck | null>(null);
  const [showStatusCheck, setShowStatusCheck] = useState(false);

  const openQuery = useQuery({
    queryKey: ["registration-open"],
    queryFn: () => api.get<RegistrationOpen>("/api/registrations/open"),
  });

  if (openQuery.isLoading) {
    return (
      <PublicFrame>
        <Spinner label="Checking whether registration is open…" />
      </PublicFrame>
    );
  }

  if (openQuery.data && !openQuery.data.open) {
    return (
      <PublicFrame>
        <div className="card text-center">
          <h2 className="text-ios-title3 font-semibold">
            Registration is closed
          </h2>
          <p className="mt-2 text-ios-subhead text-label-2">
            The IT department is not accepting OJT registrations at the moment.
            Please contact your school's OJT coordinator for the next intake.
          </p>
          <button
            className="btn-plain mt-4"
            onClick={() => setShowStatusCheck(true)}
          >
            I already have a reference code
          </button>
          {showStatusCheck ? <StatusCheck /> : null}
        </div>
      </PublicFrame>
    );
  }

  if (submitted) {
    return (
      <PublicFrame>
        <div className="card">
          <Badge className="border-success/30 bg-success-soft text-success">
            Received
          </Badge>
          <h2 className="mt-3 text-ios-title3 font-semibold">
            Thank you, your registration is in
          </h2>
          <p className="mt-2 text-ios-subhead text-label-2">
            {submitted.message}
          </p>

          <div className="mt-4 rounded-ios-md border border-separator bg-canvas p-4 text-center">
            <p className="label-section">Your reference code</p>
            <p className="tnum select-all text-ios-title2 font-semibold tracking-wide">
              {submitted.reference_code}
            </p>
            <p className="hint">
              Write this down. It is the only way to check your status before
              you are given an account.
            </p>
          </div>

          <div className="mt-4 border-t border-separator pt-4">
            <StatusCheck initialCode={submitted.reference_code} />
          </div>
        </div>
      </PublicFrame>
    );
  }

  return (
    <PublicFrame>
      <RegistrationForm
        coordinatorEmailRequired={
          openQuery.data?.coordinator_email_required ?? true
        }
        onDone={setSubmitted}
      />
      <div className="mt-4 text-center">
        <button
          className="btn-plain"
          onClick={() => setShowStatusCheck((v) => !v)}
        >
          {showStatusCheck ? "Hide status check" : "Already registered? Check your status"}
        </button>
        {showStatusCheck ? (
          <div className="card mt-3 text-left">
            <StatusCheck />
          </div>
        ) : null}
      </div>
    </PublicFrame>
  );
}

function PublicFrame({ children }: { children: React.ReactNode }) {
  return (
    <div className="min-h-screen bg-canvas px-4 py-10">
      <div className="mx-auto w-full max-w-2xl">
        <header className="mb-6 text-center">
          <span
            className="mx-auto grid h-11 w-11 place-items-center rounded-ios-md
                       bg-primary text-ios-body font-bold text-white shadow-ios"
            aria-hidden
          >
            IT
          </span>
          <h1 className="mt-3 text-ios-title1 font-semibold">
            OJT Registration
          </h1>
          <p className="mt-1 text-ios-subhead text-label-2">
            Information Technology Department &middot; On-the-Job Training
          </p>
        </header>
        {children}
        <p className="mt-8 text-center text-ios-caption text-label-3">
          Your details are used only to administer your OJT placement and are
          reviewed by the IT department before any account is created.
        </p>
      </div>
    </div>
  );
}

const FUNCTIONS = [
  "Service Desk",
  "Network Operations",
  "Desktop Support",
  "Systems Administration",
  "Security Operations",
  "Infrastructure",
  "Not sure yet",
];

function RegistrationForm({
  coordinatorEmailRequired,
  onDone,
}: {
  coordinatorEmailRequired: boolean;
  onDone: (ack: RegistrationAck) => void;
}) {
  const schools = useQuery({
    queryKey: ["public-schools"],
    queryFn: () => api.get<PublicSchool[]>("/api/schools/public"),
  });

  const [form, setForm] = useState<RegistrationInput>({
    first_name: "",
    last_name: "",
    email: "",
  });
  // "" = pick from the list, "other" = type a name not on file.
  const [schoolChoice, setSchoolChoice] = useState<string>("");

  const set = <K extends keyof RegistrationInput>(
    key: K,
    value: RegistrationInput[K],
  ) => setForm((f) => ({ ...f, [key]: value }));

  const mutation = useMutation({
    mutationFn: (payload: RegistrationInput) =>
      api.post<RegistrationAck>("/api/registrations", payload),
    onSuccess: onDone,
  });

  const schoolChosen =
    schoolChoice === "other"
      ? Boolean(form.school_name_text?.trim())
      : Boolean(schoolChoice);

  const ready = useMemo(
    () =>
      form.first_name.trim().length > 0 &&
      form.last_name.trim().length > 0 &&
      /.+@.+\..+/.test(form.email) &&
      schoolChosen &&
      (!coordinatorEmailRequired ||
        /.+@.+\..+/.test(form.coordinator_email ?? "")),
    [form, schoolChosen, coordinatorEmailRequired],
  );

  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!ready) return;
    // Send only the chosen branch, so a school picked from the list does not
    // also carry a stale typed name.
    const payload: RegistrationInput = {
      ...form,
      school_id: schoolChoice && schoolChoice !== "other"
        ? Number(schoolChoice)
        : null,
      school_name_text: schoolChoice === "other" ? form.school_name_text : null,
    };
    mutation.mutate(payload);
  };

  return (
    <form onSubmit={submit} className="space-y-4">
      {mutation.isError ? <ErrorNote error={mutation.error} /> : null}

      <section className="card">
        <h2 className="label-section">Your details</h2>
        <div className="grid gap-3 sm:grid-cols-3">
          <Field label="First name" required>
            <input
              className="input"
              value={form.first_name}
              onChange={(e) => set("first_name", e.target.value)}
              autoComplete="given-name"
              required
            />
          </Field>
          <Field label="Middle name">
            <input
              className="input"
              value={form.middle_name ?? ""}
              onChange={(e) => set("middle_name", e.target.value)}
              autoComplete="additional-name"
            />
          </Field>
          <Field label="Last name" required>
            <input
              className="input"
              value={form.last_name}
              onChange={(e) => set("last_name", e.target.value)}
              autoComplete="family-name"
              required
            />
          </Field>
        </div>

        <div className="mt-3 grid gap-3 sm:grid-cols-2">
          <Field label="Email" required>
            <input
              className="input"
              type="email"
              value={form.email}
              onChange={(e) => set("email", e.target.value)}
              autoComplete="email"
              required
            />
            <p className="hint">Used for your sign-in account once approved.</p>
          </Field>
          <Field label="Mobile number">
            <input
              className="input"
              value={form.contact_number ?? ""}
              onChange={(e) => set("contact_number", e.target.value)}
              placeholder="09XX XXX XXXX"
              autoComplete="tel"
            />
          </Field>
        </div>
      </section>

      <section className="card">
        <h2 className="label-section">Your school</h2>
        <Field label="School" required>
          <select
            className="input"
            value={schoolChoice}
            onChange={(e) => setSchoolChoice(e.target.value)}
            required
          >
            <option value="">Select your school…</option>
            {(schools.data ?? []).map((s) => (
              <option key={s.id} value={String(s.id)}>
                {s.short_name ? `${s.name} (${s.short_name})` : s.name}
              </option>
            ))}
            <option value="other">My school is not listed</option>
          </select>
        </Field>

        {schoolChoice === "other" ? (
          <Field label="School name" required>
            <input
              className="input"
              value={form.school_name_text ?? ""}
              onChange={(e) => set("school_name_text", e.target.value)}
              placeholder="Full name of your school"
              required
            />
            <p className="hint">
              The IT department will add it when reviewing your registration.
            </p>
          </Field>
        ) : null}

        <div className="mt-3 grid gap-3 sm:grid-cols-2">
          <Field label="Course">
            <input
              className="input"
              value={form.course ?? ""}
              onChange={(e) => set("course", e.target.value)}
              placeholder="BS Information Technology"
            />
          </Field>
          <Field label="Year level">
            <input
              className="input"
              value={form.year_level ?? ""}
              onChange={(e) => set("year_level", e.target.value)}
              placeholder="4th year"
            />
          </Field>
        </div>
      </section>

      <section className="card">
        <h2 className="label-section">
          Your school's OJT / IT coordinator
        </h2>
        <p className="-mt-1 mb-3 text-ios-footnote text-label-2">
          The person at your school who signs off your hours. Your completed
          timesheet is sent to them at the end of your placement, so these
          details matter.
        </p>
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label="Coordinator name">
            <input
              className="input"
              value={form.coordinator_name ?? ""}
              onChange={(e) => set("coordinator_name", e.target.value)}
              placeholder="Prof. Juan Dela Cruz"
            />
          </Field>
          <Field
            label="Coordinator email"
            required={coordinatorEmailRequired}
          >
            <input
              className="input"
              type="email"
              value={form.coordinator_email ?? ""}
              onChange={(e) => set("coordinator_email", e.target.value)}
              placeholder="ojt@school.edu.ph"
              required={coordinatorEmailRequired}
            />
          </Field>
          <Field label="Coordinator contact number">
            <input
              className="input"
              value={form.coordinator_contact ?? ""}
              onChange={(e) => set("coordinator_contact", e.target.value)}
            />
          </Field>
        </div>
      </section>

      <section className="card">
        <h2 className="label-section">Your OJT requirement</h2>
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label="Required hours">
            <input
              className="input tnum"
              type="number"
              min={1}
              max={10000}
              value={form.required_hours ?? ""}
              onChange={(e) =>
                set(
                  "required_hours",
                  e.target.value ? Number(e.target.value) : null,
                )
              }
              placeholder="486"
            />
            <p className="hint">As stated by your school. Leave blank if unsure.</p>
          </Field>
          <Field label="Preferred start date">
            <input
              className="input"
              type="date"
              value={form.ojt_start_date ?? ""}
              onChange={(e) => set("ojt_start_date", e.target.value || null)}
            />
          </Field>
          <Field label="School submission deadline">
            <input
              className="input"
              type="date"
              value={form.school_submission_deadline ?? ""}
              onChange={(e) =>
                set("school_submission_deadline", e.target.value || null)
              }
            />
            <p className="hint">
              When your school needs the signed timesheet.
            </p>
          </Field>
          <Field label="Area you are most interested in">
            <select
              className="input"
              value={form.primary_technical_function ?? ""}
              onChange={(e) =>
                set("primary_technical_function", e.target.value || null)
              }
            >
              <option value="">No preference</option>
              {FUNCTIONS.map((f) => (
                <option key={f} value={f}>
                  {f}
                </option>
              ))}
            </select>
          </Field>
        </div>
      </section>

      <section className="card">
        <h2 className="label-section">Emergency contact</h2>
        <div className="grid gap-3 sm:grid-cols-3">
          <Field label="Name">
            <input
              className="input"
              value={form.emergency_contact_name ?? ""}
              onChange={(e) => set("emergency_contact_name", e.target.value)}
            />
          </Field>
          <Field label="Number">
            <input
              className="input"
              value={form.emergency_contact_number ?? ""}
              onChange={(e) => set("emergency_contact_number", e.target.value)}
            />
          </Field>
          <Field label="Relationship">
            <input
              className="input"
              value={form.emergency_contact_relation ?? ""}
              onChange={(e) =>
                set("emergency_contact_relation", e.target.value)
              }
              placeholder="Parent"
            />
          </Field>
        </div>
        <Field label="Anything else we should know">
          <textarea
            className="input min-h-[80px]"
            value={form.applicant_notes ?? ""}
            onChange={(e) => set("applicant_notes", e.target.value)}
            maxLength={2000}
          />
        </Field>
      </section>

      <button
        className="btn-primary w-full py-3"
        type="submit"
        disabled={!ready || mutation.isPending}
      >
        {mutation.isPending ? "Submitting…" : "Submit registration"}
      </button>
      {!ready ? (
        <p className="text-center text-ios-footnote text-label-3">
          Name, email, school
          {coordinatorEmailRequired ? " and coordinator email" : ""} are
          required.
        </p>
      ) : null}
    </form>
  );
}

function Field({
  label,
  required,
  children,
}: {
  label: string;
  required?: boolean;
  children: React.ReactNode;
}) {
  return (
    <div>
      <label className="label">
        {label}
        {required ? <span className="text-danger"> *</span> : null}
      </label>
      {children}
    </div>
  );
}

const STATUS_TONE: Record<string, string> = {
  PENDING: "border-warning/30 bg-warning-soft text-warning",
  APPROVED: "border-success/30 bg-success-soft text-success",
  REJECTED: "border-danger/30 bg-danger-soft text-danger",
  WITHDRAWN: "border-separator bg-fill text-label-2",
};

const STATUS_MESSAGE: Record<string, string> = {
  PENDING:
    "Waiting for review by the IT department. You will be contacted by email.",
  APPROVED:
    "Approved. Your sign-in details are sent separately by the IT department.",
  REJECTED: "Not accepted. See the note below.",
  WITHDRAWN: "This registration was withdrawn.",
};

function StatusCheck({ initialCode = "" }: { initialCode?: string }) {
  const [code, setCode] = useState(initialCode);
  const [result, setResult] = useState<RegistrationStatusOut | null>(null);

  const mutation = useMutation({
    mutationFn: (c: string) =>
      api.get<RegistrationStatusOut>(
        `/api/registrations/status/${encodeURIComponent(c.trim())}`,
      ),
    onSuccess: setResult,
  });

  return (
    <div>
      <h3 className="label-section">Check a registration</h3>
      <div className="flex flex-wrap gap-2">
        <input
          className="input flex-1"
          value={code}
          onChange={(e) => setCode(e.target.value.toUpperCase())}
          placeholder="REG-2026-XXXXXXXX"
          aria-label="Reference code"
        />
        <button
          className="btn-tinted"
          onClick={() => code.trim() && mutation.mutate(code)}
          disabled={!code.trim() || mutation.isPending}
        >
          {mutation.isPending ? "Checking…" : "Check"}
        </button>
      </div>

      {mutation.isError ? (
        <div className="mt-3">
          <ErrorNote error={mutation.error} />
        </div>
      ) : null}

      {result ? (
        <div className="mt-3 rounded-ios-md border border-separator bg-canvas p-4">
          <div className="flex items-center justify-between gap-3">
            <span className="tnum text-ios-footnote text-label-2">
              {result.reference_code}
            </span>
            <Badge className={STATUS_TONE[result.status] ?? ""}>
              {result.status}
            </Badge>
          </div>
          <p className="mt-2 text-ios-subhead">
            {STATUS_MESSAGE[result.status] ?? result.status}
          </p>
          <p className="hint">Submitted {result.submitted_on}</p>
          {result.review_notes ? (
            <p className="mt-2 rounded-ios bg-fill p-3 text-ios-footnote text-label-2">
              {result.review_notes}
            </p>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

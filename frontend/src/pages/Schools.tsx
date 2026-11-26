import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";
import type { School, SchoolInput } from "../api/types";
import {
  Badge, Empty, ErrorNote, PageHeader, Spinner,
} from "../components/ui";
import { useAuth } from "../hooks/useAuth";

/**
 * Schools and their OJT / IT coordinators.
 *
 * The coordinator is operational data, not an address book entry: they are
 * who signs off the intern's hours and who the completed timesheet is sent to
 * when the placement ends. Without an address on file, that hand-off turns
 * into a hunt through email at exactly the point a school deadline is due.
 */
export function Schools() {
  const { can } = useAuth();
  const qc = useQueryClient();
  const [editing, setEditing] = useState<School | "new" | null>(null);
  const [showInactive, setShowInactive] = useState(false);

  const query = useQuery({
    queryKey: ["schools", showInactive],
    queryFn: () =>
      api.get<School[]>("/api/schools", { include_inactive: showInactive }),
  });

  const archive = useMutation({
    mutationFn: (id: number) => api.del(`/api/schools/${id}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["schools"] }),
  });

  const schools = query.data ?? [];
  const manage = can("school.manage");

  return (
    <div>
      <PageHeader
        title="Schools"
        subtitle="Partner schools and the coordinator who signs off each placement"
        actions={
          manage ? (
            <button className="btn-primary" onClick={() => setEditing("new")}>
              Add school
            </button>
          ) : undefined
        }
      />

      {editing ? (
        <SchoolForm
          school={editing === "new" ? null : editing}
          onClose={() => setEditing(null)}
        />
      ) : null}

      <label className="mb-4 flex items-center gap-2 text-ios-footnote text-label-2">
        <input
          type="checkbox"
          className="accent-primary"
          checked={showInactive}
          onChange={(e) => setShowInactive(e.target.checked)}
        />
        Include schools no longer offered in pickers
      </label>

      {query.isLoading ? <Spinner /> : null}
      {query.isError ? (
        <ErrorNote error={query.error} onRetry={() => query.refetch()} />
      ) : null}
      {archive.isError ? <ErrorNote error={archive.error} /> : null}

      {query.isSuccess && !schools.length ? (
        <Empty
          title="No schools on file yet"
          hint={
            manage
              ? "Add the schools you take interns from. Their names then appear on the public registration form."
              : "Ask an IT Manager to add the partner schools."
          }
        />
      ) : null}

      <div className="grid gap-4 lg:grid-cols-2">
        {schools.map((s) => (
          <article key={s.id} className="card">
            <div className="flex items-start justify-between gap-3">
              <div className="min-w-0">
                <h2 className="truncate text-ios-body font-semibold">
                  {s.name}
                </h2>
                {s.short_name ? (
                  <p className="text-ios-footnote text-label-3">
                    {s.short_name}
                  </p>
                ) : null}
              </div>
              {!s.is_active ? (
                <Badge className="border-separator bg-fill text-label-2">
                  Not offered
                </Badge>
              ) : null}
            </div>

            <dl className="mt-4 space-y-2 border-t border-separator pt-3">
              <Row label="OJT / IT coordinator" value={s.coordinator_name} />
              <Row
                label="Coordinator email"
                value={s.coordinator_email}
                href={
                  s.coordinator_email ? `mailto:${s.coordinator_email}` : null
                }
                missingHint="Needed to return the signed timesheet"
              />
              <Row label="Contact number" value={s.coordinator_contact} />
              <Row
                label="Default required hours"
                value={
                  s.default_required_hours
                    ? `${s.default_required_hours} h`
                    : null
                }
              />
              <Row label="Address" value={s.address} />
            </dl>

            <div className="mt-4 flex items-center justify-between gap-3 border-t border-separator pt-3">
              <p className="text-ios-footnote text-label-2">
                <span className="tnum font-medium text-label">
                  {s.active_intern_count}
                </span>{" "}
                active
                {s.intern_count !== s.active_intern_count ? (
                  <span className="text-label-3">
                    {" "}
                    of {s.intern_count} total
                  </span>
                ) : null}
              </p>
              {manage ? (
                <div className="flex gap-2">
                  <button
                    className="btn-ghost btn-sm"
                    onClick={() => setEditing(s)}
                  >
                    Edit
                  </button>
                  {s.is_active ? (
                    <button
                      className="btn-ghost btn-sm text-danger"
                      onClick={() => {
                        if (
                          window.confirm(
                            `Stop offering ${s.name} in pickers? Existing interns and their timesheets are kept.`,
                          )
                        ) {
                          archive.mutate(s.id);
                        }
                      }}
                      disabled={archive.isPending}
                    >
                      Deactivate
                    </button>
                  ) : null}
                </div>
              ) : null}
            </div>
          </article>
        ))}
      </div>
    </div>
  );
}

function Row({
  label,
  value,
  href,
  missingHint,
}: {
  label: string;
  value: string | null | undefined;
  href?: string | null;
  missingHint?: string;
}) {
  return (
    <div className="flex items-baseline justify-between gap-3">
      <dt className="shrink-0 text-ios-footnote text-label-2">{label}</dt>
      <dd className="min-w-0 truncate text-right text-ios-footnote">
        {value ? (
          href ? (
            <a className="text-primary hover:underline" href={href}>
              {value}
            </a>
          ) : (
            <span className="text-label">{value}</span>
          )
        ) : (
          <span className="text-label-3" title={missingHint}>
            Not set{missingHint ? " ·" : ""}
            {missingHint ? (
              <span className="text-warning"> {missingHint}</span>
            ) : null}
          </span>
        )}
      </dd>
    </div>
  );
}

const EMPTY: SchoolInput = {
  name: "",
  short_name: null,
  address: null,
  coordinator_name: null,
  coordinator_email: null,
  coordinator_contact: null,
  default_required_hours: null,
  remarks: null,
};

function SchoolForm({
  school,
  onClose,
}: {
  school: School | null;
  onClose: () => void;
}) {
  const qc = useQueryClient();
  const [form, setForm] = useState<SchoolInput>(
    school
      ? {
          name: school.name,
          short_name: school.short_name,
          address: school.address,
          coordinator_name: school.coordinator_name,
          coordinator_email: school.coordinator_email,
          coordinator_contact: school.coordinator_contact,
          default_required_hours: school.default_required_hours,
          remarks: school.remarks,
        }
      : EMPTY,
  );

  const set = <K extends keyof SchoolInput>(k: K, v: SchoolInput[K]) =>
    setForm((f) => ({ ...f, [k]: v }));

  const save = useMutation({
    mutationFn: (payload: SchoolInput) =>
      school
        ? api.patch<School>(`/api/schools/${school.id}`, payload)
        : api.post<School>("/api/schools", payload),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["schools"] });
      qc.invalidateQueries({ queryKey: ["public-schools"] });
      onClose();
    },
  });

  return (
    <form
      className="card mb-5"
      onSubmit={(e) => {
        e.preventDefault();
        if (form.name.trim().length >= 2) save.mutate(form);
      }}
    >
      <h2 className="mb-3 text-ios-body font-semibold">
        {school ? `Edit ${school.name}` : "Add a school"}
      </h2>

      {save.isError ? (
        <div className="mb-3">
          <ErrorNote error={save.error} />
        </div>
      ) : null}

      <div className="grid gap-3 sm:grid-cols-2">
        <div className="sm:col-span-2">
          <label className="label">
            School name<span className="text-danger"> *</span>
          </label>
          <input
            className="input"
            value={form.name}
            onChange={(e) => set("name", e.target.value)}
            placeholder="Holy Angel University"
            required
            minLength={2}
          />
        </div>
        <div>
          <label className="label">Short name</label>
          <input
            className="input"
            value={form.short_name ?? ""}
            onChange={(e) => set("short_name", e.target.value || null)}
            placeholder="HAU"
          />
        </div>
        <div>
          <label className="label">Default required hours</label>
          <input
            className="input tnum"
            type="number"
            min={1}
            max={10000}
            value={form.default_required_hours ?? ""}
            onChange={(e) =>
              set(
                "default_required_hours",
                e.target.value ? Number(e.target.value) : null,
              )
            }
            placeholder="486"
          />
          <p className="hint">
            Pre-filled for new interns from this school.
          </p>
        </div>
        <div className="sm:col-span-2">
          <label className="label">Address</label>
          <input
            className="input"
            value={form.address ?? ""}
            onChange={(e) => set("address", e.target.value || null)}
          />
        </div>
      </div>

      <h3 className="label-section mt-5">OJT / IT coordinator</h3>
      <p className="-mt-1 mb-3 text-ios-footnote text-label-2">
        Who at the school signs off hours and receives the completed timesheet.
      </p>
      <div className="grid gap-3 sm:grid-cols-3">
        <div>
          <label className="label">Name</label>
          <input
            className="input"
            value={form.coordinator_name ?? ""}
            onChange={(e) => set("coordinator_name", e.target.value || null)}
            placeholder="Prof. Juan Dela Cruz"
          />
        </div>
        <div>
          <label className="label">Email</label>
          <input
            className="input"
            type="email"
            value={form.coordinator_email ?? ""}
            onChange={(e) => set("coordinator_email", e.target.value || null)}
            placeholder="ojt@school.edu.ph"
          />
        </div>
        <div>
          <label className="label">Contact number</label>
          <input
            className="input"
            value={form.coordinator_contact ?? ""}
            onChange={(e) => set("coordinator_contact", e.target.value || null)}
          />
        </div>
      </div>

      <div className="mt-3">
        <label className="label">Remarks</label>
        <textarea
          className="input min-h-[60px]"
          value={form.remarks ?? ""}
          onChange={(e) => set("remarks", e.target.value || null)}
        />
      </div>

      <div className="mt-4 flex gap-2">
        <button
          className="btn-primary"
          type="submit"
          disabled={save.isPending || form.name.trim().length < 2}
        >
          {save.isPending ? "Saving…" : school ? "Save changes" : "Add school"}
        </button>
        <button className="btn-ghost" type="button" onClick={onClose}>
          Cancel
        </button>
      </div>
    </form>
  );
}

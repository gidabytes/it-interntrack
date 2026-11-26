import { useState } from "react";
import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api } from "../api/client";
import type { Intern, Page } from "../api/types";
import {
  Badge,
  Empty,
  ErrorNote,
  PageHeader,
  Spinner,
  Table,
} from "../components/ui";
import { dateOnly, titleCase } from "../lib/format";
import { toneClass } from "../lib/status";

const STATUS_TONE: Record<string, Parameters<typeof toneClass>[0]> = {
  ACTIVE: "ok",
  PENDING: "info",
  ON_LEAVE: "warn",
  COMPLETED: "ok",
  WITHDRAWN: "bad",
  INACTIVE: "muted",
};

export function Interns() {
  const [q, setQ] = useState("");
  const [status, setStatus] = useState("");

  const { data, isLoading, error, refetch } = useQuery({
    queryKey: ["interns", q, status],
    queryFn: () =>
      api.get<Page<Intern>>("/api/interns", {
        q: q || undefined,
        status: status || undefined,
        page_size: 200,
      }),
  });

  return (
    <>
      <PageHeader
        title="Interns"
        subtitle="Search covers name and intern code. Email is encrypted at rest, so it is not substring-searchable."
      />

      <div className="mb-4 flex flex-wrap gap-2">
        <input
          className="input max-w-xs"
          placeholder="Search name or intern code…"
          value={q}
          onChange={(e) => setQ(e.target.value)}
        />
        <select
          className="input max-w-[11rem]"
          value={status}
          onChange={(e) => setStatus(e.target.value)}
        >
          <option value="">All statuses</option>
          {Object.keys(STATUS_TONE).map((s) => (
            <option key={s} value={s}>
              {titleCase(s)}
            </option>
          ))}
        </select>
      </div>

      {isLoading ? (
        <Spinner />
      ) : error ? (
        <ErrorNote error={error} onRetry={refetch} />
      ) : !data?.items.length ? (
        <Empty
          title="No interns found"
          hint={q || status ? "Try clearing the filters." : "Register an intern to begin."}
        />
      ) : (
        <>
          <p className="mb-2 text-xs text-label-3">
            {data.total} intern{data.total === 1 ? "" : "s"}
          </p>
          <Table
            head={
              <tr>
                <th className="th">Code</th>
                <th className="th">Name</th>
                <th className="th">Status</th>
                <th className="th">Course</th>
                <th className="th">Start</th>
                <th className="th">School deadline</th>
                <th className="th text-right">Required</th>
                <th className="th">Function</th>
              </tr>
            }
          >
            {data.items.map((i) => (
              <tr key={i.id} className="hover:bg-fill">
                <td className="td font-mono text-xs text-label-2">
                  {i.intern_code}
                </td>
                <td className="td">
                  <Link
                    to={`/interns/${i.id}`}
                    className="font-medium text-label hover:text-primary"
                  >
                    {i.display_name}
                  </Link>
                </td>
                <td className="td">
                  <Badge className={toneClass(STATUS_TONE[i.status] ?? "muted")}>
                    {titleCase(i.status)}
                  </Badge>
                </td>
                <td className="td text-label-2">{i.course ?? "—"}</td>
                <td className="td text-label-2">{dateOnly(i.ojt_start_date)}</td>
                <td className="td">
                  {i.school_submission_deadline ? (
                    dateOnly(i.school_submission_deadline)
                  ) : (
                    <span className="text-xs text-warning">not set</span>
                  )}
                </td>
                <td className="td tnum text-right">{i.required_hours} h</td>
                <td className="td text-xs text-label-2">
                  {titleCase(i.primary_technical_function)}
                </td>
              </tr>
            ))}
          </Table>
        </>
      )}
    </>
  );
}

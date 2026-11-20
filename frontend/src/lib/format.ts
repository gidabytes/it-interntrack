/** Display helpers. Business times are Asia/Manila; the API sends ISO strings. */

export const BUSINESS_TZ = "Asia/Manila";

export function hhmm(minutes: number | null | undefined): string {
  if (minutes === null || minutes === undefined) return "—";
  const sign = minutes < 0 ? "-" : "";
  const m = Math.abs(Math.round(minutes));
  return `${sign}${Math.floor(m / 60)}:${String(m % 60).padStart(2, "0")}`;
}

export function hours(minutes: number | null | undefined, places = 2): string {
  if (minutes === null || minutes === undefined) return "—";
  return (minutes / 60).toFixed(places);
}

/** Clock time of an instant, rendered in the business timezone. */
export function clock(iso: string | null | undefined): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleTimeString("en-GB", {
    hour: "2-digit",
    minute: "2-digit",
    timeZone: BUSINESS_TZ,
  });
}

export function dateTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleString("en-GB", {
    day: "2-digit",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
    timeZone: BUSINESS_TZ,
  });
}

export function dateOnly(value: string | null | undefined): string {
  if (!value) return "—";
  // Date-only strings must not be shifted by a timezone conversion.
  const [y, m, d] = value.slice(0, 10).split("-").map(Number);
  return new Date(y, m - 1, d).toLocaleDateString("en-GB", {
    day: "2-digit",
    month: "short",
    year: "numeric",
  });
}

export function dayLabel(value: string): string {
  const [y, m, d] = value.slice(0, 10).split("-").map(Number);
  return new Date(y, m - 1, d).toLocaleDateString("en-GB", {
    weekday: "short",
    day: "2-digit",
    month: "short",
  });
}

export function pct(value: number | null | undefined, places = 1): string {
  if (value === null || value === undefined) return "—";
  return `${value.toFixed(places)}%`;
}

export function duration(minutes: number): string {
  if (minutes < 60) return `${Math.round(minutes)}m`;
  const h = Math.floor(minutes / 60);
  const m = Math.round(minutes % 60);
  return m ? `${h}h ${m}m` : `${h}h`;
}

export function titleCase(value: string | null | undefined): string {
  if (!value) return "—";
  return value
    .replace(/_/g, " ")
    .toLowerCase()
    .replace(/\b\w/g, (c) => c.toUpperCase());
}

/** Today in the business timezone, as YYYY-MM-DD. */
export function businessToday(): string {
  return new Date().toLocaleDateString("en-CA", { timeZone: BUSINESS_TZ });
}

export function addDays(isoDate: string, days: number): string {
  const [y, m, d] = isoDate.slice(0, 10).split("-").map(Number);
  const dt = new Date(Date.UTC(y, m - 1, d));
  dt.setUTCDate(dt.getUTCDate() + days);
  return dt.toISOString().slice(0, 10);
}

export function startOfMonth(isoDate: string): string {
  return `${isoDate.slice(0, 7)}-01`;
}

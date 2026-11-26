/** Colour and label mapping for the domain's status vocabularies. */

type Tone = "ok" | "warn" | "bad" | "info" | "muted";

const TONE_CLASS: Record<Tone, string> = {
  ok: "bg-success-soft text-success border-success/30",
  warn: "bg-warning-soft text-warning border-warning/30",
  bad: "bg-danger-soft text-danger border-danger/30",
  info: "bg-primary-soft text-primary-strong border-primary/30",
  muted: "bg-fill text-label-2 border-separator",
};

const ATTENDANCE_TONE: Record<string, Tone> = {
  PRESENT: "ok",
  IN_PROGRESS: "info",
  LATE: "warn",
  UNDERTIME: "warn",
  HALF_DAY: "warn",
  ABSENT: "bad",
  INCOMPLETE_TIME_RECORD: "bad",
  ON_LEAVE: "info",
  OFFICIAL_BUSINESS: "info",
  REST_DAY: "muted",
  HOLIDAY: "muted",
  NO_SCHEDULE: "muted",
  SCHEDULED: "muted",
};

const DUTY_TONE: Record<string, Tone> = {
  ON_DUTY: "ok",
  ON_BREAK: "info",
  LATE_NO_TIME_IN: "warn",
  MISSING_TIME_OUT: "bad",
  ABSENT: "bad",
  EXPECTED_TO_REPORT: "info",
  UPCOMING_SHIFT: "muted",
  COMPLETED_SHIFT: "ok",
  ON_LEAVE: "info",
  REST_DAY: "muted",
};

const RISK_TONE: Record<string, Tone> = {
  ON_TRACK: "ok",
  COMPLETED: "ok",
  AT_RISK: "warn",
  WILL_MISS_DEADLINE: "bad",
  NO_DATA: "muted",
};

const APPROVAL_TONE: Record<string, Tone> = {
  APPROVED: "ok",
  PENDING: "warn",
  REJECTED: "bad",
  CANCELLED: "muted",
  SIGNED: "ok",
  SUBMITTED: "info",
  DRAFT: "muted",
};

const BACKLOG_TONE: Record<string, Tone> = {
  CLEAR: "ok",
  PENDING: "info",
  BEHIND: "warn",
  CRITICAL: "bad",
};

export function toneClass(tone: Tone): string {
  return TONE_CLASS[tone];
}

export function attendanceTone(status: string): string {
  return TONE_CLASS[ATTENDANCE_TONE[status] ?? "muted"];
}

export function dutyTone(state: string): string {
  return TONE_CLASS[DUTY_TONE[state] ?? "muted"];
}

export function riskTone(risk: string): string {
  return TONE_CLASS[RISK_TONE[risk] ?? "muted"];
}

export function approvalTone(status: string): string {
  return TONE_CLASS[APPROVAL_TONE[status] ?? "muted"];
}

export function backlogTone(status: string): string {
  return TONE_CLASS[BACKLOG_TONE[status] ?? "muted"];
}

/** Human labels for the duty-board buckets, in the order they should appear. */
export const DUTY_ORDER: { state: string; label: string; hint: string }[] = [
  { state: "ON_DUTY", label: "On duty", hint: "Timed in and working" },
  { state: "ON_BREAK", label: "On break", hint: "Timed in, currently on break" },
  {
    state: "LATE_NO_TIME_IN",
    label: "Late, no time in",
    hint: "Past the grace period with no punch",
  },
  {
    state: "MISSING_TIME_OUT",
    label: "Missing time out",
    hint: "Shift ended, session still open",
  },
  { state: "ABSENT", label: "Absent", hint: "Shift finished with no punch at all" },
  { state: "EXPECTED_TO_REPORT", label: "Expected to report", hint: "Due in soon" },
  { state: "UPCOMING_SHIFT", label: "Upcoming shift", hint: "Later today or tomorrow" },
  { state: "COMPLETED_SHIFT", label: "Completed shift", hint: "Timed out" },
  { state: "ON_LEAVE", label: "On leave", hint: "Approved leave" },
  { state: "REST_DAY", label: "Rest day", hint: "Not scheduled" },
];

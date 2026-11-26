/** Shapes returned by the API. Hand-written to match the OpenAPI schema. */

export type Role =
  | "SYSTEM_ADMINISTRATOR"
  | "IT_MANAGER"
  | "IT_SUPERVISOR"
  | "IT_MENTOR"
  | "INTERN";

export interface CurrentUser {
  id: number;
  username: string;
  full_name: string;
  email: string;
  position_title: string | null;
  must_change_password: boolean;
  roles: { name: Role; display_name: string }[];
  permissions: string[];
  intern_id: number | null;
}

export interface TokenResponse {
  access_token: string;
  token_type: string;
  expires_in_minutes: number;
  user: CurrentUser;
}

export interface Page<T> {
  items: T[];
  total: number;
  page: number;
  page_size: number;
}

export interface Intern {
  id: number;
  intern_code: string;
  first_name: string;
  middle_name: string | null;
  last_name: string;
  full_name: string;
  display_name: string;
  contact_number: string | null;
  email: string | null;
  emergency_contact_name: string | null;
  emergency_contact_number: string | null;
  emergency_contact_relation: string | null;
  school_id: number | null;
  /** Resolved from the school record by the API. */
  school_name: string | null;
  school_short_name: string | null;
  /**
   * Who at the school signs off these hours. Prefers the name captured on the
   * intern at enrolment over the school's current coordinator.
   */
  coordinator_name: string | null;
  coordinator_email: string | null;
  coordinator_contact: string | null;
  course: string | null;
  year_level: string | null;
  ojt_start_date: string;
  expected_completion_date: string | null;
  school_submission_deadline: string | null;
  actual_completion_date: string | null;
  required_hours: number;
  department_id: number | null;
  team_id: number | null;
  supervisor_id: number | null;
  mentor_id: number | null;
  primary_technical_function: string | null;
  status: string;
  user_id: number | null;
  remarks: string | null;
}

export interface ShiftTemplate {
  id: number;
  code: string;
  name: string;
  start_time: string;
  end_time: string;
  is_overnight: boolean;
  elapsed_minutes: number;
  break_minutes: number;
  break_is_paid: boolean;
  unpaid_break_minutes: number;
  net_working_minutes: number;
  grace_period_minutes: number;
  absence_grace_minutes: number;
  max_overtime_minutes: number;
  min_rest_hours: number;
  is_active: boolean;
  is_system_default: boolean;
  effective_from: string | null;
  effective_to: string | null;
}

export interface AttendanceRecord {
  id: number;
  intern_id: number;
  business_date: string;
  session_sequence: number;
  time_in: string | null;
  time_out: string | null;
  shift_code: string | null;
  scheduled_start: string | null;
  scheduled_end: string | null;
  is_overnight_shift: boolean;
  scheduled_elapsed_minutes: number;
  scheduled_break_minutes: number;
  scheduled_net_minutes: number;
  actual_elapsed_minutes: number;
  actual_break_minutes: number;
  unpaid_break_deducted_minutes: number;
  actual_net_minutes: number;
  late_minutes: number;
  is_late: boolean;
  undertime_minutes: number;
  overtime_minutes: number;
  approved_overtime_minutes: number;
  overtime_approved: boolean;
  creditable_minutes: number;
  credit_capped: boolean;
  cap_reason: string | null;
  status: string;
  approval_status: string;
  approved_by_id: number | null;
  approved_at: string | null;
  supervisor_remarks: string | null;
  source: string;
  is_locked: boolean;
  breaks: {
    id: number;
    break_start: string;
    break_end: string | null;
    break_type: string;
    is_paid: boolean;
    duration_minutes: number;
  }[];
}

export interface DashboardKpi {
  total_active_interns: number;
  total_present_today: number;
  total_absent_today: number;
  total_late_today: number;
  total_on_duty: number;
  total_completed_ojt: number;
  total_hours_rendered: number;
  total_remaining_hours: number;
  overall_completion_percentage: number;
  attendance_compliance_percentage: number;
  pending_approval_hours: number;
  hours_rendered_in_period: number;
}

export interface Dashboard {
  generated_at: string;
  filter: {
    preset: string;
    date_from: string;
    date_to: string;
  };
  kpi: DashboardKpi;
  analytics: {
    daily_attendance: {
      date: string;
      present: number;
      absent: number;
      late: number;
      other: number;
    }[];
    hours_per_day: { date: string; hours: number }[];
    hours_rendered_vs_required: {
      required_hours: number;
      rendered_hours: number;
      remaining_hours: number;
    };
    attendance_by_team: TeamCompliance[];
    individual_progress: {
      intern_id: number;
      intern_code: string;
      intern_name: string;
      team: string | null;
      required_hours: number;
      approved_hours: number;
      remaining_hours: number;
      completion_percentage: number;
      milestone: string | null;
    }[];
    status_distribution_today: { status: string; count: number }[];
  };
}

export interface TeamCompliance {
  team_id: number;
  team_code: string;
  team_name: string;
  technical_function: string | null;
  intern_count: number;
  compliance_percentage: number;
  punctuality_percentage: number;
  absent_days: number;
  late_days: number;
  expected_days: number;
  completion_percentage: number;
  meets_target: boolean;
}

export type DutyState =
  | "ON_DUTY"
  | "ON_BREAK"
  | "LATE_NO_TIME_IN"
  | "MISSING_TIME_OUT"
  | "ABSENT"
  | "EXPECTED_TO_REPORT"
  | "UPCOMING_SHIFT"
  | "COMPLETED_SHIFT"
  | "ON_LEAVE"
  | "REST_DAY";

export interface DutyEntry {
  intern_id: number;
  intern_code: string;
  intern_name: string;
  team: string | null;
  supervisor: string | null;
  technical_function: string | null;
  state: DutyState;
  business_date: string | null;
  shift_code: string | null;
  shift_label: string | null;
  scheduled_start: string | null;
  scheduled_end: string | null;
  is_overnight: boolean;
  time_in: string | null;
  time_out: string | null;
  on_break_since: string | null;
  hours_on_duty: number;
  minutes_on_duty: number;
  minutes_remaining_in_shift: number;
  minutes_until_start: number;
  late_minutes: number;
  attendance_status: string | null;
  attendance_record_id: number | null;
  notes: string[];
}

export interface DutyBoard {
  generated_at: string;
  business_timezone?: string;
  window: { from: string; to: string };
  buckets: Record<DutyState, DutyEntry[]>;
  counts: Record<DutyState, number>;
  total_interns: number;
  on_duty_now: number;
  needs_attention: number;
}

export interface OjtProgress {
  intern_id: number;
  required_hours: number;
  approved_hours: number;
  pending_hours: number;
  actual_rendered_hours: number;
  remaining_hours: number;
  completion_percentage: number;
  raw_completion_percentage: number;
  is_complete: boolean;
  milestone: string | null;
  next_milestone: { threshold: number; label: string } | null;
  required_minutes: number;
  approved_minutes: number;
  remaining_minutes: number;
}

export type ForecastRisk =
  | "ON_TRACK"
  | "AT_RISK"
  | "WILL_MISS_DEADLINE"
  | "COMPLETED"
  | "NO_DATA";

export interface Forecast {
  intern_id: number;
  intern_code: string;
  intern_name: string;
  team: string | null;
  supervisor: string | null;
  required_hours: number;
  approved_hours: number;
  remaining_hours: number;
  completion_percentage: number;
  avg_hours_per_week: number;
  scheduled_hours_remaining: number;
  projected_completion_date: string | null;
  projection_basis: string;
  school_submission_deadline: string | null;
  expected_completion_date: string | null;
  risk: ForecastRisk;
  days_to_deadline: number | null;
  days_late: number | null;
  hours_short_at_deadline: number | null;
  recommended_extra_hours_per_week: number | null;
  notes: string[];
}

export interface InternProgressPage {
  intern: {
    id: number;
    intern_code: string;
    name: string;
    status: string;
    team: string | null;
    supervisor: string | null;
    mentor: string | null;
    ojt_start_date: string;
    school_submission_deadline: string | null;
  };
  progress: OjtProgress;
  attendance: {
    days_present: number;
    absences: number;
    late_arrivals: number;
    undertime_instances: number;
    half_days: number;
    incomplete_records: number;
    total_overtime_hours: number;
    attendance_rate: number;
  };
  forecast: Forecast;
  milestones: { threshold: number; label: string; reached: boolean }[];
}

export interface ComplianceDashboard {
  generated_at: string;
  filter: { preset: string; date_from: string; date_to: string };
  summary: {
    active_interns: number;
    attendance_compliance_percentage: number;
    attendance_target: number;
    meets_attendance_target: boolean;
    punctuality_percentage: number;
    ojt_completion_percentage: number;
    total_absent_days: number;
    total_late_days: number;
    total_pending_approvals: number;
    overdue_approvals: number;
    supervisors_behind: number;
    interns_at_risk: number;
    interns_near_completion: number;
    interns_with_excessive_absences: number;
  };
  by_team: TeamCompliance[];
  approval_backlog: {
    warning_threshold_days: number;
    total_pending: number;
    supervisors: {
      supervisor_id: number;
      supervisor_name: string;
      intern_count: number;
      pending_attendance: number;
      pending_corrections: number;
      pending_leave: number;
      pending_timesheets: number;
      overdue_items: number;
      oldest_pending_days: number | null;
      total_pending: number;
      status: string;
    }[];
  };
  excessive_absences: {
    intern_id: number;
    intern_code: string;
    intern_name: string;
    team: string | null;
    supervisor: string | null;
    absences: number;
    threshold: number;
    window_days: number;
  }[];
  at_risk_interns: Forecast[];
  near_completion: Forecast[];
  alerts: { severity: string; message: string }[];
}

export interface ExposureMatrix {
  generated_at: string;
  areas: string[];
  area_keys: string[];
  area_summary: {
    area: string;
    label: string;
    interns_with_exposure: number;
    total_hours: number;
    interns_untouched: number;
    exposure_rate: number;
  }[];
  interns: {
    intern_id: number;
    intern_code: string;
    intern_name: string;
    team: string | null;
    total_logged_hours: number;
    core_areas_met: number;
    core_areas_total: number;
    gaps: string[];
    areas: Record<
      string,
      { hours: number; coverage_percentage: number; proficiency: string | null }
    >;
  }[];
  intern_count: number;
  department_gaps: {
    area: string;
    label: string;
    exposure_rate: number;
    interns_untouched: number;
  }[];
}

export interface Timesheet {
  id: number;
  intern_id: number;
  reference_no: string;
  period_type: string;
  period_start: string;
  period_end: string;
  revision: number;
  total_days_scheduled: number;
  total_days_present: number;
  total_days_absent: number;
  total_days_late: number;
  total_scheduled_minutes: number;
  total_actual_minutes: number;
  total_creditable_minutes: number;
  total_late_minutes: number;
  total_undertime_minutes: number;
  total_overtime_minutes: number;
  cumulative_approved_minutes: number;
  required_minutes: number;
  remaining_minutes: number;
  status: string;
  signed_by_name: string | null;
  signed_by_position: string | null;
  signature_hash: string | null;
  is_locked: boolean;
  superseded_by_id: number | null;
}

export interface ScheduleDay {
  business_date: string;
  intern_id: number;
  shift_code: string | null;
  shift_name: string | null;
  scheduled_start: string | null;
  scheduled_end: string | null;
  is_overnight: boolean;
  scheduled_elapsed_minutes: number;
  scheduled_break_minutes: number;
  scheduled_net_minutes: number;
  is_rest_day: boolean;
  is_holiday: boolean;
  holiday_name: string | null;
  is_on_leave: boolean;
  is_working_day: boolean;
  assignment_type: string | null;
  source: string;
}

export interface Calendar {
  intern_id: number;
  intern_code: string;
  date_from: string;
  date_to: string;
  working_days: number;
  rest_days: number;
  holidays: number;
  leave_days: number;
  scheduled_hours: number;
  days: ScheduleDay[];
}

export interface MyStatus {
  intern_code: string;
  server_time: string;
  is_timed_in: boolean;
  on_break_since: string | null;
  open_session: AttendanceRecord | null;
  today_schedule: ScheduleDay;
  can_time_in: boolean;
  can_time_out: boolean;
}

export interface ReportCatalogue {
  reports: { key: string; title: string }[];
  formats: string[];
  presets: string[];
}

// --- Schools and the school-side OJT / IT coordinator ----------------------

export interface School {
  id: number;
  name: string;
  short_name: string | null;
  address: string | null;
  /** The school-side contact who signs off OJT hours. */
  coordinator_name: string | null;
  coordinator_email: string | null;
  coordinator_contact: string | null;
  default_required_hours: number | null;
  is_active: boolean;
  remarks: string | null;
  intern_count: number;
  active_intern_count: number;
}

export interface PublicSchool {
  id: number;
  name: string;
  short_name: string | null;
}

export type SchoolInput = Omit<
  School,
  "id" | "is_active" | "intern_count" | "active_intern_count"
>;

// --- Intern self-registration ---------------------------------------------

export type RegistrationStatus =
  | "PENDING"
  | "APPROVED"
  | "REJECTED"
  | "WITHDRAWN";

export interface RegistrationInput {
  first_name: string;
  middle_name?: string | null;
  last_name: string;
  email: string;
  contact_number?: string | null;
  school_id?: number | null;
  school_name_text?: string | null;
  course?: string | null;
  year_level?: string | null;
  coordinator_name?: string | null;
  coordinator_email?: string | null;
  coordinator_contact?: string | null;
  required_hours?: number | null;
  ojt_start_date?: string | null;
  school_submission_deadline?: string | null;
  primary_technical_function?: string | null;
  emergency_contact_name?: string | null;
  emergency_contact_number?: string | null;
  emergency_contact_relation?: string | null;
  applicant_notes?: string | null;
}

export interface RegistrationAck {
  reference_code: string;
  status: RegistrationStatus;
  message: string;
}

export interface RegistrationStatusOut {
  reference_code: string;
  status: RegistrationStatus;
  submitted_on: string;
  reviewed_on: string | null;
  review_notes: string | null;
}

export interface Registration {
  id: number;
  reference_code: string;
  status: RegistrationStatus;
  applicant_name: string;
  first_name: string;
  middle_name: string | null;
  last_name: string;
  email: string;
  contact_number: string | null;
  school_id: number | null;
  school_name_text: string | null;
  school_label: string | null;
  course: string | null;
  year_level: string | null;
  coordinator_name: string | null;
  coordinator_email: string | null;
  coordinator_contact: string | null;
  required_hours: number | null;
  ojt_start_date: string | null;
  school_submission_deadline: string | null;
  primary_technical_function: string | null;
  emergency_contact_name: string | null;
  emergency_contact_number: string | null;
  emergency_contact_relation: string | null;
  applicant_notes: string | null;
  submitted_ip: string | null;
  created_at: string;
  reviewed_at: string | null;
  reviewed_by_user_id: number | null;
  review_notes: string | null;
  created_intern_id: number | null;
  /** Set when the submitted address already belongs to an intern. */
  duplicate_of_intern_id: number | null;
}

export interface RegistrationApproved {
  intern_id: number;
  intern_code: string;
  username: string | null;
  /** Shown once and never recoverable. */
  temporary_password: string | null;
  message: string;
}

export interface RegistrationOpen {
  open: boolean;
  coordinator_email_required: boolean;
}

// --- Login activity -------------------------------------------------------

export interface AuditEntry {
  id: number;
  created_at: string;
  /** The endpoint names this `actor`, not `actor_username`. */
  actor: string | null;
  actor_roles: string | null;
  action: string;
  entity_type: string;
  entity_id: number | null;
  summary: string;
  before_state: Record<string, unknown> | null;
  after_state: Record<string, unknown> | null;
  ip_address: string | null;
  user_agent: string | null;
}

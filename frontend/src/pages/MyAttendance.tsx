import { Attendance } from "./Attendance";

/**
 * The intern's own attendance. The list endpoint already scopes results to the
 * signed-in intern, so the same screen serves both roles; only the framing
 * differs, and the approve actions are hidden because interns lack the
 * permission.
 */
export function MyAttendance() {
  return <Attendance />;
}

import { NavLink, Outlet, useNavigate } from "react-router-dom";
import { useAuth } from "../hooks/useAuth";
import { useTheme } from "../hooks/useTheme";

interface NavItem {
  to: string;
  label: string;
  /** Any one of these permissions grants access to the link. */
  perms?: string[];
  group: "Monitoring" | "People" | "Records" | "Administration";
}

const NAV: NavItem[] = [
  { to: "/", label: "Dashboard", group: "Monitoring", perms: ["dashboard.read"] },
  {
    to: "/duty-board",
    label: "Duty Board",
    group: "Monitoring",
    perms: ["dutyboard.read"],
  },
  {
    to: "/compliance",
    label: "Compliance",
    group: "Monitoring",
    perms: ["dashboard.compliance"],
  },
  {
    to: "/interns",
    label: "Interns",
    group: "People",
    perms: ["intern.read", "intern.read.assigned"],
  },
  {
    to: "/exposure",
    label: "Technical Exposure",
    group: "People",
    perms: ["exposure.read"],
  },
  {
    to: "/attendance",
    label: "Attendance",
    group: "Records",
    perms: ["attendance.read", "attendance.read.assigned"],
  },
  {
    to: "/timesheets",
    label: "Timesheets",
    group: "Records",
    perms: ["timesheet.read"],
  },
  { to: "/reports", label: "Reports", group: "Records", perms: ["report.read"] },
  {
    to: "/shifts",
    label: "Shift Templates",
    group: "Administration",
    perms: ["shift.read"],
  },
  {
    to: "/settings",
    label: "Settings",
    group: "Administration",
    perms: ["settings.manage"],
  },
];

const GROUPS = ["Monitoring", "People", "Records", "Administration"] as const;

export function Shell() {
  const { user, signOut, can, hasRole } = useAuth();
  const { theme, toggle } = useTheme();
  const navigate = useNavigate();

  const isIntern = hasRole("INTERN");
  const visible = NAV.filter((n) => !n.perms || can(...n.perms));

  return (
    <div className="flex min-h-screen">
      {/* Sidebar */}
      <aside className="hidden w-60 shrink-0 border-r border-edge bg-sidebar md:block">
        <div className="flex h-14 items-center gap-2 border-b border-edge px-5">
          <span className="grid h-7 w-7 place-items-center rounded-md bg-primary text-sm font-bold text-white">
            IT
          </span>
          <span className="text-sm font-semibold text-slate-100">InternTrack</span>
        </div>

        <nav className="px-3 py-4">
          {isIntern ? (
            <div className="mb-4">
              <p className="px-2 pb-1.5 text-[10px] font-semibold uppercase tracking-wider text-slate-500">
                My OJT
              </p>
              {[
                { to: "/me/clock", label: "Time Clock" },
                { to: "/me/progress", label: "My Progress" },
                { to: "/me/schedule", label: "My Schedule" },
                { to: "/me/attendance", label: "My Attendance" },
              ].map((i) => (
                <SideLink key={i.to} to={i.to} label={i.label} />
              ))}
            </div>
          ) : null}

          {GROUPS.map((g) => {
            const items = visible.filter((n) => n.group === g);
            if (!items.length) return null;
            return (
              <div key={g} className="mb-4">
                <p className="px-2 pb-1.5 text-[10px] font-semibold uppercase tracking-wider text-slate-500">
                  {g}
                </p>
                {items.map((i) => (
                  <SideLink key={i.to} to={i.to} label={i.label} />
                ))}
              </div>
            );
          })}
        </nav>
      </aside>

      {/* Main */}
      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex h-14 items-center justify-between gap-3 border-b border-edge bg-sidebar/60 px-4 backdrop-blur md:px-6">
          <div className="flex items-center gap-3 md:hidden">
            <span className="grid h-7 w-7 place-items-center rounded-md bg-primary text-xs font-bold text-white">
              IT
            </span>
            <span className="text-sm font-semibold">InternTrack</span>
          </div>

          <div className="ml-auto flex items-center gap-3">
            <button
              className="btn-ghost px-2.5 py-1.5 text-xs"
              onClick={toggle}
              title={`Switch to ${theme === "dark" ? "light" : "dark"} mode`}
            >
              {theme === "dark" ? "Light" : "Dark"}
            </button>

            <div className="hidden text-right sm:block">
              <p className="text-sm font-medium leading-tight text-slate-200">
                {user?.full_name}
              </p>
              <p className="text-xs leading-tight text-slate-500">
                {user?.roles.map((r) => r.display_name).join(", ")}
              </p>
            </div>

            <button
              className="btn-ghost px-2.5 py-1.5 text-xs"
              onClick={() => {
                signOut();
                navigate("/login", { replace: true });
              }}
            >
              Sign out
            </button>
          </div>
        </header>

        {/* Mobile nav */}
        <div className="flex gap-1 overflow-x-auto border-b border-edge px-3 py-2 md:hidden">
          {(isIntern
            ? [
                { to: "/me/clock", label: "Clock" },
                { to: "/me/progress", label: "Progress" },
              ]
            : []
          )
            .concat(visible.map((v) => ({ to: v.to, label: v.label })))
            .map((i) => (
              <NavLink
                key={i.to}
                to={i.to}
                className={({ isActive }) =>
                  `shrink-0 rounded-md px-3 py-1.5 text-xs ${
                    isActive
                      ? "bg-primary text-white"
                      : "text-slate-400 hover:bg-white/5"
                  }`
                }
              >
                {i.label}
              </NavLink>
            ))}
        </div>

        <main className="min-w-0 flex-1 p-4 md:p-6">
          <Outlet />
        </main>
      </div>
    </div>
  );
}

function SideLink({ to, label }: { to: string; label: string }) {
  return (
    <NavLink
      to={to}
      end={to === "/"}
      className={({ isActive }) =>
        `block rounded-lg px-2.5 py-2 text-sm transition ${
          isActive
            ? "bg-primary-soft font-medium text-primary"
            : "text-slate-400 hover:bg-white/5 hover:text-slate-200"
        }`
      }
    >
      {label}
    </NavLink>
  );
}

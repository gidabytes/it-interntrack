import { useEffect, useRef, useState } from "react";
import { NavLink, Outlet, useNavigate } from "react-router-dom";
import { useAuth } from "../hooks/useAuth";
import { useTheme, type ThemePreference } from "../hooks/useTheme";

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
    to: "/schools",
    label: "Schools",
    group: "People",
    perms: ["school.manage"],
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
    to: "/registrations",
    label: "Registrations",
    group: "Administration",
    perms: ["registration.read"],
  },
  {
    to: "/login-activity",
    label: "Login Activity",
    group: "Administration",
    perms: ["audit.read"],
  },
  {
    to: "/settings",
    label: "Settings",
    group: "Administration",
    perms: ["settings.manage"],
  },
];

const GROUPS = ["Monitoring", "People", "Records", "Administration"] as const;

const INTERN_LINKS = [
  { to: "/me/clock", label: "Time Clock" },
  { to: "/me/progress", label: "My Progress" },
  { to: "/me/schedule", label: "My Schedule" },
  { to: "/me/attendance", label: "My Attendance" },
];

export function Shell() {
  const { user, signOut, can, hasRole } = useAuth();
  const navigate = useNavigate();
  const [mobileNavOpen, setMobileNavOpen] = useState(false);

  const isIntern = hasRole("INTERN");
  const visible = NAV.filter((n) => !n.perms || can(...n.perms));

  return (
    <div className="flex min-h-screen bg-canvas">
      {/* Sidebar. Glass over the page rather than a solid panel, which is what
          gives the layered look; it sits above the scrolling content. */}
      <aside
        className="glass sticky top-0 hidden h-screen w-64 shrink-0
                   border-r border-separator md:block"
      >
        <div className="flex h-14 items-center gap-2.5 px-5">
          <AppMark />
          <span className="text-ios-subhead font-semibold text-label">
            InternTrack
          </span>
        </div>

        <nav className="space-y-5 overflow-y-auto px-3 pb-6 pt-2">
          {isIntern ? (
            <NavGroup title="My OJT">
              {INTERN_LINKS.map((i) => (
                <SideLink key={i.to} to={i.to} label={i.label} />
              ))}
            </NavGroup>
          ) : null}

          {GROUPS.map((g) => {
            const items = visible.filter((n) => n.group === g);
            if (!items.length) return null;
            return (
              <NavGroup key={g} title={g}>
                {items.map((i) => (
                  <SideLink key={i.to} to={i.to} label={i.label} />
                ))}
              </NavGroup>
            );
          })}
        </nav>
      </aside>

      <div className="flex min-w-0 flex-1 flex-col">
        {/* Header. Also glass, and sticky, so content passes beneath it. */}
        <header
          className="glass sticky top-0 z-30 flex h-14 items-center
                     justify-between gap-3 border-b border-separator px-4 md:px-6"
        >
          <div className="flex items-center gap-2.5 md:hidden">
            <button
              className="btn-ghost btn-sm"
              onClick={() => setMobileNavOpen((v) => !v)}
              aria-expanded={mobileNavOpen}
              aria-label="Menu"
            >
              Menu
            </button>
            <AppMark />
          </div>

          <div className="ml-auto flex items-center gap-2">
            <AppearanceMenu />

            <NavLink
              to="/change-password"
              className="hidden rounded-ios px-2 py-1 text-right transition-colors
                         hover:bg-fill sm:block"
              title="Change your password"
            >
              <p className="text-ios-footnote font-medium leading-tight text-label">
                {user?.full_name}
              </p>
              <p className="text-ios-caption2 leading-tight text-label-3">
                {user?.roles.map((r) => r.display_name).join(", ")}
              </p>
            </NavLink>

            <button
              className="btn-ghost btn-sm"
              onClick={() => {
                signOut();
                navigate("/login", { replace: true });
              }}
            >
              Sign out
            </button>
          </div>
        </header>

        {/* Mobile navigation, as a disclosed sheet rather than a cramped
            horizontal scroller that hid most of the destinations. */}
        {mobileNavOpen ? (
          <nav className="space-y-4 border-b border-separator bg-elevated px-4 py-4 md:hidden">
            {isIntern ? (
              <NavGroup title="My OJT">
                {INTERN_LINKS.map((i) => (
                  <SideLink
                    key={i.to}
                    to={i.to}
                    label={i.label}
                    onNavigate={() => setMobileNavOpen(false)}
                  />
                ))}
              </NavGroup>
            ) : null}
            {GROUPS.map((g) => {
              const items = visible.filter((n) => n.group === g);
              if (!items.length) return null;
              return (
                <NavGroup key={g} title={g}>
                  {items.map((i) => (
                    <SideLink
                      key={i.to}
                      to={i.to}
                      label={i.label}
                      onNavigate={() => setMobileNavOpen(false)}
                    />
                  ))}
                </NavGroup>
              );
            })}
          </nav>
        ) : null}

        <main className="min-w-0 flex-1 p-4 md:p-6">
          <Outlet />
        </main>
      </div>
    </div>
  );
}

function AppMark() {
  return (
    <span
      className="grid h-7 w-7 place-items-center rounded-[0.5rem] bg-primary
                 text-ios-caption font-bold text-white shadow-sm"
      aria-hidden
    >
      IT
    </span>
  );
}

function NavGroup({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div>
      <p className="px-2.5 pb-1 text-ios-caption2 font-semibold uppercase
                    tracking-[0.06em] text-label-3">
        {title}
      </p>
      <div className="space-y-0.5">{children}</div>
    </div>
  );
}

function SideLink({
  to,
  label,
  onNavigate,
}: {
  to: string;
  label: string;
  onNavigate?: () => void;
}) {
  return (
    <NavLink
      to={to}
      end={to === "/"}
      onClick={onNavigate}
      className={({ isActive }) =>
        `block rounded-ios px-2.5 py-2 text-ios-subhead transition-colors
         duration-150 ease-ios ${
           isActive
             ? "bg-primary-soft font-semibold text-primary-strong"
             : "text-label-2 hover:bg-fill hover:text-label"
         }`
      }
    >
      {label}
    </NavLink>
  );
}

const THEME_OPTIONS: { value: ThemePreference; label: string }[] = [
  { value: "light", label: "Light" },
  { value: "dark", label: "Dark" },
  { value: "system", label: "Auto" },
];

/**
 * Appearance popover: light/dark/auto plus the Liquid Glass clarity slider.
 *
 * The slider mirrors the system-wide control iOS 27 introduced. It matters
 * here for a practical reason rather than a cosmetic one: this application is
 * often left open on a shared NOC display, where a more tinted material is
 * easier to read at a distance than a clear one.
 */
function AppearanceMenu() {
  const { preference, setPreference, clarity, setClarity } = useTheme();
  const [open, setOpen] = useState(false);
  const box = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (box.current && !box.current.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  return (
    <div className="relative" ref={box}>
      <button
        className="btn-ghost btn-sm"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        aria-haspopup="true"
      >
        Appearance
      </button>

      {open ? (
        <div
          className="glass absolute right-0 top-full z-40 mt-2 w-64 rounded-ios-md
                     border border-separator p-3 shadow-ios-lg"
          role="dialog"
          aria-label="Appearance"
        >
          <p className="label-section">Theme</p>
          <div className="segmented w-full">
            {THEME_OPTIONS.map((o) => (
              <button
                key={o.value}
                className={`segmented-item flex-1 ${
                  preference === o.value ? "segmented-item-active" : ""
                }`}
                onClick={() => setPreference(o.value)}
                aria-pressed={preference === o.value}
              >
                {o.label}
              </button>
            ))}
          </div>

          <p className="label-section mt-4">Glass clarity</p>
          <input
            type="range"
            min={0.4}
            max={1}
            step={0.02}
            value={clarity}
            onChange={(e) => setClarity(Number(e.target.value))}
            className="h-1.5 w-full cursor-pointer appearance-none rounded-full
                       bg-fill-2 accent-primary"
            aria-label="Glass clarity, from clearest to tinted"
          />
          <div className="mt-1.5 flex justify-between text-ios-caption2 text-label-3">
            <span>Clearest</span>
            <span>Tinted</span>
          </div>
          <p className="hint">
            More tint is easier to read on a shared display.
          </p>
        </div>
      ) : null}
    </div>
  );
}

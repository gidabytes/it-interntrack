import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";

const THEME_KEY = "interntrack.theme";
const CLARITY_KEY = "interntrack.clarity";

/** "system" follows the operating system, as iOS and macOS do by default. */
export type ThemePreference = "system" | "light" | "dark";
export type Resolved = "light" | "dark";

/**
 * Translucency of the Liquid Glass surfaces, 0 = clearest, 1 = fully tinted.
 *
 * iOS 27 added a system-wide slider for exactly this, after the transparency
 * introduced in 26 drew complaints about legibility. The same trade-off
 * applies to a duty board on a wall-mounted screen, so it is exposed rather
 * than fixed: a glance-from-across-the-room display wants it tinted.
 */
export type Clarity = number;

const DEFAULT_CLARITY = 0.72;

function readTheme(): ThemePreference {
  try {
    const v = localStorage.getItem(THEME_KEY);
    if (v === "light" || v === "dark" || v === "system") return v;
    // Migrate the older key, which stored only "dark" or "light".
  } catch {
    /* storage unavailable, e.g. private browsing */
  }
  return "system";
}

function readClarity(): Clarity {
  try {
    const v = Number(localStorage.getItem(CLARITY_KEY));
    if (Number.isFinite(v) && v >= 0.4 && v <= 1) return v;
  } catch {
    /* storage unavailable */
  }
  return DEFAULT_CLARITY;
}

function systemPrefersDark(): boolean {
  return (
    typeof window !== "undefined" &&
    typeof window.matchMedia === "function" &&
    window.matchMedia("(prefers-color-scheme: dark)").matches
  );
}

interface ThemeValue {
  /** What the user chose, which may be "system". */
  preference: ThemePreference;
  /** What is actually on screen. */
  theme: Resolved;
  setPreference: (p: ThemePreference) => void;
  /** Cycles light -> dark -> system, for the header button. */
  cycle: () => void;
  clarity: Clarity;
  setClarity: (c: Clarity) => void;
}

const Ctx = createContext<ThemeValue | null>(null);

export function ThemeProvider({ children }: { children: ReactNode }) {
  const [preference, setPreference] = useState<ThemePreference>(readTheme);
  const [clarity, setClarityState] = useState<Clarity>(readClarity);
  const [systemDark, setSystemDark] = useState(systemPrefersDark);

  // Follow the OS while the preference is "system". Without this listener the
  // app would only pick up a system change on reload.
  useEffect(() => {
    if (typeof window.matchMedia !== "function") return;
    const mq = window.matchMedia("(prefers-color-scheme: dark)");
    const onChange = (e: MediaQueryListEvent) => setSystemDark(e.matches);
    mq.addEventListener("change", onChange);
    return () => mq.removeEventListener("change", onChange);
  }, []);

  const theme: Resolved =
    preference === "system" ? (systemDark ? "dark" : "light") : preference;

  useEffect(() => {
    const root = document.documentElement;
    root.classList.toggle("dark", theme === "dark");
    root.classList.toggle("light", theme === "light");
    try {
      localStorage.setItem(THEME_KEY, preference);
    } catch {
      /* storage unavailable */
    }
  }, [theme, preference]);

  useEffect(() => {
    document.documentElement.style.setProperty(
      "--glass-opacity",
      String(clarity),
    );
    try {
      localStorage.setItem(CLARITY_KEY, String(clarity));
    } catch {
      /* storage unavailable */
    }
  }, [clarity]);

  const setClarity = useCallback((c: Clarity) => {
    setClarityState(Math.min(1, Math.max(0.4, c)));
  }, []);

  const cycle = useCallback(() => {
    setPreference((p) =>
      p === "light" ? "dark" : p === "dark" ? "system" : "light",
    );
  }, []);

  const value = useMemo(
    () => ({ preference, theme, setPreference, cycle, clarity, setClarity }),
    [preference, theme, cycle, clarity, setClarity],
  );

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useTheme(): ThemeValue {
  const ctx = useContext(Ctx);
  if (!ctx) throw new Error("useTheme must be used inside <ThemeProvider>");
  return ctx;
}

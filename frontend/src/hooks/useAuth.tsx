import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import {
  api,
  getToken,
  setToken,
  setUnauthorizedHandler,
} from "../api/client";
import type { CurrentUser, Role, TokenResponse } from "../api/types";

interface AuthState {
  user: CurrentUser | null;
  loading: boolean;
  signIn: (username: string, password: string) => Promise<void>;
  signOut: () => void;
  /** Re-read the current user, e.g. after a password change clears the flag. */
  refresh: () => Promise<void>;
  /** True when the user holds any of the given permission codes. */
  can: (...codes: string[]) => boolean;
  hasRole: (...roles: Role[]) => boolean;
}

const AuthContext = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<CurrentUser | null>(null);
  const [loading, setLoading] = useState(true);

  const signOut = useCallback(() => {
    setToken(null);
    setUser(null);
  }, []);

  // A 401 from any request means the token expired or the account was
  // deactivated mid-session; drop straight back to the sign-in screen rather
  // than leaving a half-broken UI.
  useEffect(() => {
    setUnauthorizedHandler(signOut);
    return () => setUnauthorizedHandler(null);
  }, [signOut]);

  // Restore an existing session on load.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      if (!getToken()) {
        setLoading(false);
        return;
      }
      try {
        const me = await api.get<CurrentUser>("/api/auth/me");
        if (!cancelled) setUser(me);
      } catch {
        setToken(null);
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const refresh = useCallback(async () => {
    const me = await api.get<CurrentUser>("/api/auth/me");
    setUser(me);
  }, []);

  const signIn = useCallback(async (username: string, password: string) => {
    const res = await api.post<TokenResponse>("/api/auth/login", {
      username,
      password,
    });
    setToken(res.access_token);
    setUser(res.user);
  }, []);

  const value = useMemo<AuthState>(() => {
    const permissions = new Set(user?.permissions ?? []);
    const roles = new Set((user?.roles ?? []).map((r) => r.name));
    return {
      user,
      loading,
      signIn,
      signOut,
      refresh,
      can: (...codes: string[]) => codes.some((c) => permissions.has(c)),
      hasRole: (...wanted: Role[]) => wanted.some((r) => roles.has(r)),
    };
  }, [user, loading, signIn, signOut, refresh]);

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthState {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used inside AuthProvider");
  return ctx;
}

import { createContext, useContext, useEffect, useState, type ReactNode } from "react";
import { api } from "../api/client";
import type { User } from "../types";

interface AuthContextValue {
  user: User | null;
  loading: boolean;
  login: (username: string, password: string, totpCode?: string) => Promise<User>;
  logout: () => void;
  // Re-fetches /me/ and updates the cached user in place -- used after a
  // self-service change like patching visible_partners (see
  // CustomersPage's partner filter) so the rest of the app picks up the
  // new value without requiring a full page reload.
  refreshUser: () => Promise<void>;
}

const AuthContext = createContext<AuthContextValue | undefined>(undefined);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const token = localStorage.getItem("access_token");
    if (!token) {
      setLoading(false);
      return;
    }
    api
      .get<User>("/me/")
      .then((res) => setUser(res.data))
      .catch(() => {
        localStorage.removeItem("access_token");
        localStorage.removeItem("refresh_token");
      })
      .finally(() => setLoading(false));
  }, []);

  async function login(username: string, password: string, totpCode?: string) {
    // totp_code is only sent when the caller has one (2FA-enabled accounts
    // resubmit with it after the first attempt signals it's required) —
    // the backend treats a missing/blank code as "no 2FA code given".
    const res = await api.post("/token/", { username, password, totp_code: totpCode || "" });
    localStorage.setItem("access_token", res.data.access);
    localStorage.setItem("refresh_token", res.data.refresh);
    const me: User = { ...res.data.user, customer_id: res.data.customer_id };
    setUser(me);
    return me;
  }

  function logout() {
    // Tell the server first, while the token is still valid, so the
    // sign-out reaches the activity log. Deliberately not awaited and
    // deliberately swallowing failure: signing out has to work with the
    // network down or the token already expired, and somebody unable to
    // sign out is a far worse failure than a missing log line.
    api.post("/sign-out/").catch(() => {});
    localStorage.removeItem("access_token");
    localStorage.removeItem("refresh_token");
    setUser(null);
  }

  async function refreshUser() {
    const token = localStorage.getItem("access_token");
    if (!token) return;
    try {
      const res = await api.get<User>("/me/");
      setUser(res.data);
    } catch {
      // Ignore -- if the token's gone stale this will surface on the next
      // real request anyway, no need to force a logout from a background
      // refresh call.
    }
  }

  return <AuthContext.Provider value={{ user, loading, login, logout, refreshUser }}>{children}</AuthContext.Provider>;
}

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within AuthProvider");
  return ctx;
}

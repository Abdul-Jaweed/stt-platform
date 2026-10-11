/**
 * Who the caller is, for the whole app.
 *
 * The session lives on the server as an HttpOnly cookie; this context only
 * caches what `GET /v1/auth/me` said and re-asks after a login or a logout.
 * Two facts decide every redirect in the app:
 *
 * * `loginRequired` — the server's `ui_auth_enabled`. With it off (the local
 *   developer flow) nobody is asked to sign in.
 * * `authenticated` — a session was accepted.
 *
 * `canEnterApp` is the only thing a route guard needs to read.
 */
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
} from "react";
import type { ReactNode } from "react";

import { ApiError } from "../api/errors";
import type { SessionInfo } from "../api/types";
import { useApi } from "../api/context";

export type SessionStatus = "loading" | "ready";

export interface SessionState {
  status: SessionStatus;
  session: SessionInfo | null;
  authenticated: boolean;
  loginRequired: boolean;
  canEnterApp: boolean;
  loginError: string | null;
  login(username: string, password: string): Promise<boolean>;
  logout(): Promise<void>;
  refresh(): Promise<void>;
}

const SessionContext = createContext<SessionState | null>(null);

export function SessionProvider({ children }: { children: ReactNode }) {
  const api = useApi();
  const [status, setStatus] = useState<SessionStatus>("loading");
  const [session, setSession] = useState<SessionInfo | null>(null);
  const [loginRequired, setLoginRequired] = useState(true);
  const [loginError, setLoginError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      const info = await api.getSession();
      setSession(info.authenticated ? info : null);
      setLoginRequired(info.ui_auth_enabled);
    } catch {
      // A 401 means "sign in"; an unreachable API also lands here. Either way
      // the shell renders and the page that needs data reports the real error.
      setSession(null);
      setLoginRequired(true);
    } finally {
      setStatus("ready");
    }
  }, [api]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const login = useCallback(
    async (username: string, password: string) => {
      setLoginError(null);
      try {
        const result = await api.login(username, password);
        setSession({
          authenticated: true,
          username: result.username,
          tenant: result.tenant,
          ui_auth_enabled: true,
          expires_at: result.expires_at,
        });
        return true;
      } catch (error) {
        setLoginError(
          error instanceof ApiError
            ? error.message
            : "Sign-in failed. Please try again.",
        );
        return false;
      }
    },
    [api],
  );

  const logout = useCallback(async () => {
    try {
      await api.logout();
    } catch {
      // Clearing the cookie can fail on a dead API; the UI still signs out.
    }
    setSession(null);
    setLoginError(null);
  }, [api]);

  const value = useMemo<SessionState>(
    () => ({
      status,
      session,
      authenticated: session?.authenticated === true,
      loginRequired,
      canEnterApp: !loginRequired || session?.authenticated === true,
      loginError,
      login,
      logout,
      refresh,
    }),
    [status, session, loginRequired, loginError, login, logout, refresh],
  );

  return (
    <SessionContext.Provider value={value}>{children}</SessionContext.Provider>
  );
}

export function useSession(): SessionState {
  const value = useContext(SessionContext);
  if (!value) {
    throw new Error("useSession must be used within a <SessionProvider>");
  }
  return value;
}

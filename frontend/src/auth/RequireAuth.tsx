/**
 * The one route guard. It never guesses: while the server's answer is still
 * in flight it says so, and an anonymous visitor is sent to the login page
 * with the path they wanted, so signing in lands them where they were going.
 */
import { SpinnerGap } from "@phosphor-icons/react";
import type { ReactNode } from "react";
import { Navigate, useLocation } from "react-router-dom";

import { useSession } from "../auth/context";

export default function RequireAuth({ children }: { children: ReactNode }) {
  const { status, canEnterApp } = useSession();
  const location = useLocation();

  if (status === "loading") {
    return (
      <div className="session-loading" role="status" aria-busy="true">
        <SpinnerGap size={18} className="spin" aria-hidden="true" />
        <span>Checking your session…</span>
      </div>
    );
  }

  if (!canEnterApp) {
    const next = `${location.pathname}${location.search}`;
    return <Navigate to={`/login?next=${encodeURIComponent(next)}`} replace />;
  }

  return <>{children}</>;
}

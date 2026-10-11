import {
  ChartBar,
  ListChecks,
  SignOut,
  UploadSimple,
  UserCircle,
} from "@phosphor-icons/react";
import { Link, NavLink, Outlet, useNavigate } from "react-router-dom";

import { useSession } from "../auth/context";
import BrandMark from "./BrandMark";
import ThemeToggle from "./ThemeToggle";

export default function Layout() {
  const { session, logout } = useSession();
  const navigate = useNavigate();

  async function onSignOut() {
    // Leave the console first: dropping the session while the guarded route
    // is still mounted would race the guard's own redirect to /login.
    navigate("/", { replace: true });
    await logout();
  }

  return (
    <div className="app">
      <header className="app-header">
        <Link className="brand" to="/upload">
          <span className="brand-mark" aria-hidden="true">
            <BrandMark />
          </span>
          <span className="brand-text">
            <h1 className="brand-name">STT Platform</h1>
            <span className="brand-tag">provider-neutral transcription</span>
          </span>
        </Link>
        <div className="header-actions">
          <nav aria-label="Main">
            <NavLink to="/upload">
              <UploadSimple size={16} aria-hidden="true" />
              Upload
            </NavLink>
            <NavLink to="/providers">
              <ListChecks size={16} aria-hidden="true" />
              Providers
            </NavLink>
            <NavLink to="/evaluations">
              <ChartBar size={16} aria-hidden="true" />
              Evaluations
            </NavLink>
          </nav>
          <div className="user-area">
            <ThemeToggle />
            {session?.username && (
              <span
                className="user-chip"
                data-testid="session-user"
                aria-label={`Signed in as ${session.username}, tenant ${session.tenant}`}
                title={`Signed in as ${session.username} · tenant ${session.tenant}`}
              >
                <UserCircle size={14} aria-hidden="true" />
                {session.username}
              </span>
            )}
            <button
              type="button"
              className="button ghost sign-out"
              onClick={onSignOut}
            >
              <SignOut size={16} aria-hidden="true" />
              Sign out
            </button>
          </div>
        </div>
      </header>
      <main>
        <Outlet />
      </main>
      <footer className="app-footer">
        <span>
          This app consumes only the platform&apos;s <code>/v1</code> API — the
          backend can be replaced without touching the UI.
        </span>
      </footer>
    </div>
  );
}

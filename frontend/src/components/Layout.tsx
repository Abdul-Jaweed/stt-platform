import { NavLink, Outlet } from "react-router-dom";

export default function Layout() {
  return (
    <div className="app">
      <header className="app-header">
        <div className="brand">
          <span className="brand-mark" aria-hidden="true">
            ◉
          </span>
          <span className="brand-text">
            <h1 className="brand-name">STT Platform</h1>
            <span className="brand-tag">provider-neutral transcription</span>
          </span>
        </div>
        <nav aria-label="Main">
          <NavLink to="/" end>
            Upload
          </NavLink>
          <NavLink to="/providers">Providers</NavLink>
          <NavLink to="/evaluations">Evaluations</NavLink>
        </nav>
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

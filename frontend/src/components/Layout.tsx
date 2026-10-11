import { NavLink, Outlet } from "react-router-dom";

export default function Layout() {
  return (
    <div className="app">
      <header className="app-header">
        <h1>STT Platform</h1>
        <nav>
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
    </div>
  );
}

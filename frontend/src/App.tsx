import { Route, Routes } from "react-router-dom";

import RequireAuth from "./auth/RequireAuth";
import Layout from "./components/Layout";
import EvaluationsPage from "./pages/EvaluationsPage";
import JobPage from "./pages/JobPage";
import LandingPage from "./pages/LandingPage";
import LoginPage from "./pages/LoginPage";
import NotFoundPage from "./pages/NotFoundPage";
import ProvidersPage from "./pages/ProvidersPage";
import TranscriptPage from "./pages/TranscriptPage";
import UploadPage from "./pages/UploadPage";

/**
 * Two public pages, one guarded console.
 *
 * `/` is the marketing page (always public: a signed-in visitor can still
 * read it), `/login` is the way in, and everything else sits behind
 * `RequireAuth` — including the catch-all, which stays public so an unknown
 * path answers "not found" instead of bouncing through a login.
 */
export default function App() {
  return (
    <Routes>
      <Route path="/" element={<LandingPage />} />
      <Route path="/login" element={<LoginPage />} />
      <Route
        element={
          <RequireAuth>
            <Layout />
          </RequireAuth>
        }
      >
        <Route path="/upload" element={<UploadPage />} />
        <Route path="/jobs/:id" element={<JobPage />} />
        <Route path="/jobs/:id/transcript" element={<TranscriptPage />} />
        <Route path="/providers" element={<ProvidersPage />} />
        <Route path="/evaluations" element={<EvaluationsPage />} />
      </Route>
      <Route path="*" element={<NotFoundPage />} />
    </Routes>
  );
}

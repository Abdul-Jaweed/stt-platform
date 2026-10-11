import { Route, Routes } from "react-router-dom";

import Layout from "./components/Layout";
import EvaluationsPage from "./pages/EvaluationsPage";
import JobPage from "./pages/JobPage";
import NotFoundPage from "./pages/NotFoundPage";
import ProvidersPage from "./pages/ProvidersPage";
import TranscriptPage from "./pages/TranscriptPage";
import UploadPage from "./pages/UploadPage";

export default function App() {
  return (
    <Routes>
      <Route element={<Layout />}>
        <Route path="/" element={<UploadPage />} />
        <Route path="/jobs/:id" element={<JobPage />} />
        <Route path="/jobs/:id/transcript" element={<TranscriptPage />} />
        <Route path="/providers" element={<ProvidersPage />} />
        <Route path="/evaluations" element={<EvaluationsPage />} />
        <Route path="*" element={<NotFoundPage />} />
      </Route>
    </Routes>
  );
}

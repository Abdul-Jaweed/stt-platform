import { Route, Routes } from "react-router-dom";

import JobPage from "./pages/JobPage";
import TranscriptPage from "./pages/TranscriptPage";
import UploadPage from "./pages/UploadPage";

export default function App() {
  return (
    <div className="app">
      <header className="app-header">
        <h1>STT Platform</h1>
      </header>
      <main>
        <Routes>
          <Route path="/" element={<UploadPage />} />
          <Route path="/jobs/:id" element={<JobPage />} />
          <Route path="/jobs/:id/transcript" element={<TranscriptPage />} />
        </Routes>
      </main>
    </div>
  );
}

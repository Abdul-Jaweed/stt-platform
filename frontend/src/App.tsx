import { Route, Routes } from "react-router-dom";

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
        </Routes>
      </main>
    </div>
  );
}

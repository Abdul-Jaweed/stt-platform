import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";

import { createApiClient } from "./api/client";
import { ApiProvider } from "./api/context";
import App from "./App";
import "./styles.css";

const apiBaseUrl =
  import.meta.env.VITE_API_BASE_URL ?? "http://127.0.0.1:8200";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <ApiProvider client={createApiClient(apiBaseUrl)}>
      <BrowserRouter>
        <App />
      </BrowserRouter>
    </ApiProvider>
  </StrictMode>,
);

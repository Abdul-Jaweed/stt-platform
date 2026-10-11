import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";

import { createApiClient } from "./api/client";
import { ApiProvider } from "./api/context";
import App from "./App";
import { SessionProvider } from "./auth/context";
import { applyTheme, resolveInitialTheme } from "./lib/theme";
import "./styles.css";

// Same rule as the pre-paint script in index.html; applying it here too keeps
// the two in lockstep even if the inline script is ever stripped.
applyTheme(resolveInitialTheme());

const apiBaseUrl =
  import.meta.env.VITE_API_BASE_URL ?? "http://127.0.0.1:8200";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <ApiProvider client={createApiClient(apiBaseUrl)}>
      <SessionProvider>
        <BrowserRouter>
          <App />
        </BrowserRouter>
      </SessionProvider>
    </ApiProvider>
  </StrictMode>,
);

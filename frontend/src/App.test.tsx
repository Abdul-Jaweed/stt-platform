import { render, screen } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { MemoryRouter } from "react-router-dom";

import { createApiClient } from "./api/client";
import { ApiProvider } from "./api/context";
import { server } from "./test/server";
import App from "./App";

const BASE = "http://127.0.0.1:8200";

function renderApp(path = "/") {
  server.use(
    http.get(`${BASE}/v1/providers`, () =>
      HttpResponse.json({ providers: [] }),
    ),
  );
  return render(
    <ApiProvider client={createApiClient(BASE)}>
      <MemoryRouter initialEntries={[path]}>
        <App />
      </MemoryRouter>
    </ApiProvider>,
  );
}

test("renders the app shell with navigation", () => {
  renderApp();
  expect(
    screen.getByRole("heading", { name: /stt platform/i }),
  ).toBeInTheDocument();
  expect(screen.getByRole("link", { name: /providers/i })).toBeInTheDocument();
  expect(
    screen.getByRole("link", { name: /evaluations/i }),
  ).toBeInTheDocument();
});

test("unknown route renders not-found", () => {
  renderApp("/nope");
  expect(screen.getByText(/not found/i)).toBeInTheDocument();
});

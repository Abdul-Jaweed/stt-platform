import { render, screen } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { MemoryRouter } from "react-router-dom";

import { createApiClient } from "./api/client";
import { ApiProvider } from "./api/context";
import { server } from "./test/server";
import App from "./App";

test("renders the app shell", async () => {
  server.use(
    http.get("http://127.0.0.1:8200/v1/providers", () =>
      HttpResponse.json({ providers: [] }),
    ),
  );
  render(
    <ApiProvider client={createApiClient("http://127.0.0.1:8200")}>
      <MemoryRouter>
        <App />
      </MemoryRouter>
    </ApiProvider>,
  );
  expect(
    screen.getByRole("heading", { name: /stt platform/i }),
  ).toBeInTheDocument();
});

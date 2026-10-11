import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";

import { createApiClient } from "../api/client";
import { ApiProvider } from "../api/context";
import { server } from "../test/server";
import EvaluationsPage from "./EvaluationsPage";

const BASE = "http://127.0.0.1:8200";

test("evaluations view explains the phase-6 gap on 404", async () => {
  server.use(
    http.get(`${BASE}/v1/evaluations/e-1`, () =>
      HttpResponse.json(
        { error: { code: "NOT_FOUND", message: "unknown route" } },
        { status: 404 },
      ),
    ),
  );

  render(
    <ApiProvider client={createApiClient(BASE)}>
      <EvaluationsPage />
    </ApiProvider>,
  );

  await userEvent.type(
    screen.getByLabelText(/evaluation id/i),
    "e-1",
  );
  await userEvent.click(screen.getByRole("button", { name: /load/i }));

  expect(await screen.findByText(/phase 6/i)).toBeInTheDocument();
});

test("a non-404 failure renders the api error", async () => {
  server.use(
    http.get(`${BASE}/v1/evaluations/e-2`, () =>
      HttpResponse.json(
        { error: { code: "INTERNAL_ERROR", message: "boom" } },
        { status: 500 },
      ),
    ),
  );

  render(
    <ApiProvider client={createApiClient(BASE)}>
      <EvaluationsPage />
    </ApiProvider>,
  );

  await userEvent.type(
    screen.getByLabelText(/evaluation id/i),
    "e-2",
  );
  await userEvent.click(screen.getByRole("button", { name: /load/i }));

  expect(await screen.findByRole("alert")).toHaveTextContent(/INTERNAL_ERROR/);
});

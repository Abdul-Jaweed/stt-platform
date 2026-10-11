import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { MemoryRouter, Route, Routes } from "react-router-dom";

import { createApiClient } from "../api/client";
import { ApiProvider } from "../api/context";
import type { JobRecord, JobStatus } from "../api/types";
import { server } from "../test/server";
import JobPage from "./JobPage";

const BASE = "http://127.0.0.1:8200";

function job(status: JobStatus, extra: Partial<JobRecord> = {}): JobRecord {
  return {
    id: "j-1",
    status,
    attempt_count: 0,
    options: {},
    created_at: "2026-10-11T00:00:00Z",
    updated_at: "2026-10-11T00:00:00Z",
    ...extra,
  };
}

function renderJob(id = "j-1") {
  return render(
    <ApiProvider client={createApiClient(BASE)}>
      <MemoryRouter initialEntries={[`/jobs/${id}`]}>
        <Routes>
          <Route path="/jobs/:id" element={<JobPage pollIntervalMs={20} />} />
          <Route
            path="/jobs/:id/transcript"
            element={<div>transcript page</div>}
          />
        </Routes>
      </MemoryRouter>
    </ApiProvider>,
  );
}

test("polls until SUCCEEDED, then stops and offers the transcript", async () => {
  let calls = 0;
  server.use(
    http.get(`${BASE}/v1/transcriptions/j-1`, () => {
      calls += 1;
      return HttpResponse.json(job(calls >= 2 ? "SUCCEEDED" : "PROCESSING"));
    }),
  );

  renderJob();

  expect(await screen.findByText("SUCCEEDED")).toBeInTheDocument();
  const callsAtTerminal = calls;
  await new Promise((resolve) => setTimeout(resolve, 80));
  expect(calls).toBe(callsAtTerminal);
  expect(
    screen.getByRole("link", { name: /view transcript/i }),
  ).toBeInTheDocument();
});

test("shows the failure code and offers retry when retryable", async () => {
  let retried = false;
  server.use(
    http.get(`${BASE}/v1/transcriptions/j-1`, () =>
      HttpResponse.json(
        retried
          ? job("QUEUED")
          : job("FAILED", {
              failure: {
                code: "PROVIDER_ERROR",
                message: "provider crashed",
                retryable: true,
              },
            }),
      ),
    ),
    http.post(`${BASE}/v1/transcriptions/j-1/retry`, () => {
      retried = true;
      return HttpResponse.json(job("QUEUED"));
    }),
  );

  renderJob();

  expect(await screen.findByText(/PROVIDER_ERROR/)).toBeInTheDocument();
  expect(screen.getByText(/provider crashed/)).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: /retry/i }));

  expect(await screen.findByText("QUEUED")).toBeInTheDocument();
});

test("cancel calls the api and shows the new status", async () => {
  let cancelled = false;
  server.use(
    http.get(`${BASE}/v1/transcriptions/j-1`, () =>
      HttpResponse.json(job(cancelled ? "CANCELLED" : "QUEUED")),
    ),
    http.post(`${BASE}/v1/transcriptions/j-1/cancel`, () => {
      cancelled = true;
      return HttpResponse.json(job("CANCELLED"));
    }),
  );

  renderJob();

  expect(await screen.findByText("QUEUED")).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: /cancel/i }));

  expect(await screen.findByText("CANCELLED")).toBeInTheDocument();
});

test("unknown job renders a not-found state", async () => {
  server.use(
    http.get(`${BASE}/v1/transcriptions/missing`, () =>
      HttpResponse.json(
        {
          error: {
            code: "JOB_NOT_FOUND",
            message: "job not found: 'missing'",
          },
        },
        { status: 404 },
      ),
    ),
  );

  renderJob("missing");

  expect(await screen.findByRole("alert")).toHaveTextContent(/JOB_NOT_FOUND/);
  expect(screen.getByRole("alert")).toHaveTextContent(
    /job not found: 'missing'/,
  );
});

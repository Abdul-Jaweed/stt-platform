import { render, screen, within } from "@testing-library/react";
import { http, HttpResponse } from "msw";

import { createApiClient } from "../api/client";
import { ApiProvider } from "../api/context";
import type { ProviderInfo } from "../api/types";
import { server } from "../test/server";
import ProvidersPage from "./ProvidersPage";

const BASE = "http://127.0.0.1:8200";

const providers: ProviderInfo[] = [
  {
    id: "faster-whisper",
    name: "Faster-Whisper (local)",
    is_local: true,
    capabilities: {
      languages: ["en", "hi", "es"],
      language_detection: true,
      segment_timestamps: true,
      word_timestamps: true,
      diarization: false,
      streaming: false,
      batch: true,
      async_processing: false,
      confidence_scores: true,
      formats: ["wav", "mp3"],
      max_duration_s: 3600,
    },
    health: { status: "ok", detail: "cuda_devices=1" },
  },
  {
    id: "deepgram",
    name: "Deepgram",
    is_local: false,
    capabilities: {
      languages: ["en"],
      language_detection: true,
      segment_timestamps: true,
      word_timestamps: false,
      diarization: true,
      streaming: true,
      batch: true,
      async_processing: true,
      confidence_scores: true,
      formats: ["wav"],
    },
    health: { status: "degraded", detail: "rate limited" },
  },
];

test("providers table shows capability flags and health", async () => {
  server.use(
    http.get(`${BASE}/v1/providers`, () =>
      HttpResponse.json({ providers }),
    ),
  );

  render(
    <ApiProvider client={createApiClient(BASE)}>
      <ProvidersPage />
    </ApiProvider>,
  );

  const localRow = await screen.findByRole("row", {
    name: /faster-whisper \(local\)/i,
  });
  expect(within(localRow).getByText("ok")).toBeInTheDocument();
  expect(within(localRow).getByText("3")).toBeInTheDocument();
  expect(within(localRow).getAllByText("yes").length).toBeGreaterThan(0);

  const cloudRow = screen.getByRole("row", { name: /deepgram/i });
  expect(within(cloudRow).getByText("degraded")).toBeInTheDocument();
  expect(within(cloudRow).getByText("no")).toBeInTheDocument();
  expect(within(cloudRow).getByText("1")).toBeInTheDocument();
});

test("provider failure renders an error with retry", async () => {
  let calls = 0;
  server.use(
    http.get(`${BASE}/v1/providers`, () => {
      calls += 1;
      if (calls === 1) {
        return HttpResponse.json(
          { error: { code: "INTERNAL_ERROR", message: "boom" } },
          { status: 500 },
        );
      }
      return HttpResponse.json({ providers });
    }),
  );

  render(
    <ApiProvider client={createApiClient(BASE)}>
      <ProvidersPage />
    </ApiProvider>,
  );

  expect(await screen.findByRole("alert")).toHaveTextContent(/INTERNAL_ERROR/);
  const { default: userEvent } = await import("@testing-library/user-event");
  await userEvent.click(screen.getByRole("button", { name: /retry/i }));

  expect(
    await screen.findByRole("row", { name: /deepgram/i }),
  ).toBeInTheDocument();
});

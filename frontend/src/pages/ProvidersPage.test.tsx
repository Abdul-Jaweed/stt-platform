import { render, screen, within } from "@testing-library/react";
import { http, HttpResponse } from "msw";

import { createApiClient } from "../api/client";
import { ApiProvider } from "../api/context";
import type { PlatformLimits } from "../api/types";
import type { ProviderInfo } from "../api/types";
import { server } from "../test/server";
import ProvidersPage from "./ProvidersPage";

const BASE = "http://127.0.0.1:8200";

const limits: PlatformLimits = {
  max_upload_bytes: 104857600,
  max_sync_duration_s: 180,
  max_async_duration_s: 14400,
  max_live_preview_duration_s: 120,
};

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
      max_file_bytes: 3 * 1024 ** 3,
      max_duration_s: 3600,
    },
    health: { status: "degraded", detail: "rate limited" },
  },
];

test("providers table shows capability flags and health", async () => {
  server.use(
    http.get(`${BASE}/v1/providers`, () =>
      HttpResponse.json({ providers, limits }),
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
      return HttpResponse.json({ providers, limits });
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

test("limits are shown the way the provider states them, or not at all", async () => {
  server.use(
    http.get(`${BASE}/v1/providers`, () =>
      HttpResponse.json({ providers, limits }),
    ),
  );

  render(
    <ApiProvider client={createApiClient(BASE)}>
      <ProvidersPage />
    </ApiProvider>,
  );

  // faster-whisper documents no file cap: the cell says so instead of
  // pretending zero or infinity.
  const localRow = await screen.findByRole("row", { name: /faster-whisper/i });
  expect(within(localRow).getByText("no limit documented")).toBeInTheDocument();

  // Deepgram's 3 GB is a vendor number, printed as one.
  const cloudRow = screen.getByRole("row", { name: /deepgram/i });
  expect(within(cloudRow).getByText("3.0 GB")).toBeInTheDocument();

  // ...and the deployment's own caps are stated next to the table, distinct
  // from the providers'.
  const note = screen.getByText(/this deployment accepts uploads up to/i);
  expect(note).toHaveTextContent("100.0 MB");
  expect(note).toHaveTextContent("4:00:00.0");
});

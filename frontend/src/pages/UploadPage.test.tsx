import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { MemoryRouter, Route, Routes, useParams } from "react-router-dom";

import { createApiClient } from "../api/client";
import { ApiProvider } from "../api/context";
import type { ProviderInfo } from "../api/types";
import { server } from "../test/server";
import UploadPage from "./UploadPage";

const BASE = "http://127.0.0.1:8200";

function makeProvider(
  id: string,
  name: string,
  isLocal: boolean,
): ProviderInfo {
  return {
    id,
    name,
    is_local: isLocal,
    capabilities: {
      languages: ["en"],
      language_detection: true,
      segment_timestamps: true,
      word_timestamps: true,
      diarization: false,
      streaming: false,
      batch: true,
      async_processing: false,
      confidence_scores: true,
      formats: ["wav"],
    },
    health: { status: "ok" },
  };
}

const providersBody = {
  providers: [
    makeProvider("faster-whisper", "Faster-Whisper (local)", true),
    makeProvider("deepgram", "Deepgram", false),
  ],
};

function JobProbe() {
  const { id } = useParams();
  return <div>job page: {id}</div>;
}

function renderUpload() {
  return render(
    <ApiProvider client={createApiClient(BASE)}>
      <MemoryRouter initialEntries={["/"]}>
        <Routes>
          <Route path="/" element={<UploadPage />} />
          <Route path="/jobs/:id" element={<JobProbe />} />
        </Routes>
      </MemoryRouter>
    </ApiProvider>,
  );
}

test("shows providers from the api, marking the local one", async () => {
  server.use(
    http.get(`${BASE}/v1/providers`, () => HttpResponse.json(providersBody)),
  );

  renderUpload();

  expect(
    await screen.findByRole("option", { name: /faster-whisper \(local\)/i }),
  ).toBeInTheDocument();
  expect(
    screen.getByRole("option", { name: /deepgram/i }),
  ).toBeInTheDocument();
});

test("submits the chosen file and navigates to the job page", async () => {
  server.use(
    http.get(`${BASE}/v1/providers`, () => HttpResponse.json(providersBody)),
    http.post(`${BASE}/v1/transcriptions`, () =>
      HttpResponse.json(
        {
          id: "j-9",
          status: "QUEUED",
          attempt_count: 0,
          options: {},
          created_at: "2026-10-11T00:00:00Z",
          updated_at: "2026-10-11T00:00:00Z",
        },
        { status: 202 },
      ),
    ),
  );

  renderUpload();

  await screen.findByRole("option", { name: /deepgram/i });
  await userEvent.upload(
    screen.getByLabelText(/audio file/i),
    new File([new Uint8Array([1, 2, 3])], "sample.wav", { type: "audio/wav" }),
  );
  await userEvent.selectOptions(
    screen.getByLabelText(/provider/i),
    "deepgram",
  );
  await userEvent.click(screen.getByRole("button", { name: /transcribe/i }));

  expect(await screen.findByText(/job page: j-9/)).toBeInTheDocument();
});

test("renders validation errors from the backend inline", async () => {
  server.use(
    http.get(`${BASE}/v1/providers`, () => HttpResponse.json(providersBody)),
    http.post(`${BASE}/v1/transcriptions`, () =>
      HttpResponse.json(
        {
          error: {
            code: "UNSUPPORTED_MEDIA",
            message: "could not identify a supported audio stream",
          },
        },
        { status: 415 },
      ),
    ),
  );

  renderUpload();

  await screen.findByRole("option", { name: /deepgram/i });
  await userEvent.upload(
    screen.getByLabelText(/audio file/i),
    new File([new Uint8Array([1])], "broken.wav", { type: "audio/wav" }),
  );
  await userEvent.click(screen.getByRole("button", { name: /transcribe/i }));

  expect(await screen.findByText(/UNSUPPORTED_MEDIA/)).toBeInTheDocument();
  expect(
    screen.getByText(/could not identify a supported audio stream/),
  ).toBeInTheDocument();
});

test("provider list failure offers a retry that recovers", async () => {
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
      return HttpResponse.json(providersBody);
    }),
  );

  renderUpload();

  await userEvent.click(
    await screen.findByRole("button", { name: /retry/i }),
  );

  expect(
    await screen.findByRole("option", { name: /deepgram/i }),
  ).toBeInTheDocument();
  expect(calls).toBe(2);
});

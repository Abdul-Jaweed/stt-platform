import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { MemoryRouter, Route, Routes } from "react-router-dom";

import { createApiClient } from "../api/client";
import { ApiProvider } from "../api/context";
import type { TranscriptResult } from "../api/types";
import { clearRetainedAudio, setRetainedAudio } from "../lib/retainedAudio";
import { server } from "../test/server";
import TranscriptPage from "./TranscriptPage";

const BASE = "http://127.0.0.1:8200";

const resultFixture: TranscriptResult = {
  job_id: "j-1",
  text: "Hello world Second line",
  language: "en",
  segments: [
    {
      start: 0,
      end: 2.5,
      text: "Hello world",
      words: [
        { start: 0, end: 1.2, text: "Hello" },
        { start: 1.2, end: 2.5, text: "world" },
      ],
    },
    { start: 2.5, end: 4, text: "Second line" },
  ],
  timestamp_granularity: "word",
  audio_duration_s: 4,
  processing_duration_s: 0.5,
  provider: "faster-whisper",
  model: "large-v3-turbo",
};

function renderPage(id = "j-1") {
  return render(
    <ApiProvider client={createApiClient(BASE)}>
      <MemoryRouter initialEntries={[`/jobs/${id}/transcript`]}>
        <Routes>
          <Route
            path="/jobs/:id/transcript"
            element={<TranscriptPage />}
          />
          <Route path="/jobs/:id" element={<div>job page</div>} />
        </Routes>
      </MemoryRouter>
    </ApiProvider>,
  );
}

function mockDownloads() {
  const created: Blob[] = [];
  const downloads: string[] = [];
  URL.createObjectURL = vi.fn((blob: Blob) => {
    created.push(blob);
    return `blob:mock-${created.length}`;
  }) as typeof URL.createObjectURL;
  URL.revokeObjectURL = vi.fn() as typeof URL.revokeObjectURL;
  vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(
    function (this: HTMLAnchorElement) {
      downloads.push(this.download);
    },
  );
  return { created, downloads };
}

function readBlob(blob: Blob): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result));
    reader.onerror = () => reject(reader.error);
    reader.readAsText(blob);
  });
}

afterEach(() => {
  clearRetainedAudio();
  vi.restoreAllMocks();
});

test("renders the transcript and downloads srt/vtt/txt", async () => {
  const { created, downloads } = mockDownloads();
  server.use(
    http.get(`${BASE}/v1/transcriptions/j-1/result`, () =>
      HttpResponse.json(resultFixture),
    ),
  );

  renderPage();

  expect(await screen.findByText("Hello world")).toBeInTheDocument();
  expect(screen.getByText("Second line")).toBeInTheDocument();

  await userEvent.click(screen.getByRole("button", { name: /download srt/i }));
  await userEvent.click(screen.getByRole("button", { name: /download vtt/i }));
  await userEvent.click(screen.getByRole("button", { name: /download txt/i }));

  expect(downloads).toEqual(["j-1.srt", "j-1.vtt", "j-1.txt"]);
  const [srtBlob, vttBlob, txtBlob] = created;
  expect(await readBlob(srtBlob)).toContain("00:00:00,000 --> 00:00:02,500");
  expect(await readBlob(vttBlob)).toContain("WEBVTT");
  expect(await readBlob(txtBlob)).toBe("Hello world\nSecond line");
});

test("clicking a segment seeks the audio element", async () => {
  setRetainedAudio(
    "j-1",
    new File([new Uint8Array([1])], "sample.wav", { type: "audio/wav" }),
  );
  server.use(
    http.get(`${BASE}/v1/transcriptions/j-1/result`, () =>
      HttpResponse.json(resultFixture),
    ),
  );

  renderPage();

  await screen.findByText("Hello world");
  const audio = document.querySelector("audio");
  expect(audio).not.toBeNull();

  await userEvent.click(screen.getByRole("button", { name: "0:02.500" }));

  expect(audio!.currentTime).toBe(2.5);
});

test("audio playback is unavailable without a retained file", async () => {
  server.use(
    http.get(`${BASE}/v1/transcriptions/j-1/result`, () =>
      HttpResponse.json(resultFixture),
    ),
  );

  renderPage();

  await screen.findByText("Hello world");
  expect(document.querySelector("audio")).toBeNull();
  expect(screen.getByText(/playback unavailable/i)).toBeInTheDocument();
});

test("a not-ready result (409) renders an error with retry", async () => {
  let calls = 0;
  server.use(
    http.get(`${BASE}/v1/transcriptions/j-1/result`, () => {
      calls += 1;
      if (calls === 1) {
        return HttpResponse.json(
          {
            error: {
              code: "JOB_NOT_SUCCEEDED",
              message: "job 'j-1' is not SUCCEEDED (status: PROCESSING)",
            },
          },
          { status: 409 },
        );
      }
      return HttpResponse.json(resultFixture);
    }),
  );

  renderPage();

  expect(await screen.findByRole("alert")).toHaveTextContent(
    /JOB_NOT_SUCCEEDED/,
  );

  await userEvent.click(screen.getByRole("button", { name: /retry/i }));

  expect(await screen.findByText("Hello world")).toBeInTheDocument();
});

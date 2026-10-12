/**
 * The recorder panel: start → tick → stop → hand the clip to the page.
 *
 * The component owns no submission logic — it produces a File and tells the
 * page, so the recorded clip travels the exact same upload path as a chosen
 * file (and gets the same retention, playback and error handling).
 */
import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import { createApiClient } from "../api/client";
import { ApiProvider } from "../api/context";
import {
  FakeMediaRecorder,
  installRecorderSupport,
  removeRecorderSupport,
} from "../test/mediaRecorder";
import { server } from "../test/server";
import { FakeWebSocket } from "../test/webSocket";
import Recorder from "./Recorder";

const BASE = "http://127.0.0.1:8200";

/**
 * By default the live socket cannot be opened (jsdom has no server behind it),
 * so the panel falls back to the HTTP preview -- exactly the path the older
 * tests here exercise. `{ streaming: true }` swaps in a socket the test drives.
 */
function renderRecorder(
  props: Partial<Parameters<typeof Recorder>[0]> = {},
  options: { streaming?: boolean } = {},
) {
  const onRecorded = props.onRecorded ?? vi.fn();
  const openSocket = options.streaming
    ? (url: string) => new FakeWebSocket(url) as unknown as WebSocket
    : () => {
        throw new Error("WebSocket is not available in jsdom");
      };
  const view = render(
    <ApiProvider client={createApiClient(BASE)}>
      <Recorder onRecorded={onRecorded} openSocket={openSocket} {...props} />
    </ApiProvider>,
  );
  return { ...view, onRecorded };
}

beforeEach(() => {
  installRecorderSupport();
});

afterEach(() => {
  removeRecorderSupport();
});

describe("Recorder", () => {
  test("records and hands the page a file", async () => {
    const onRecorded = vi.fn();
    renderRecorder({ onRecorded });

    await userEvent.click(
      screen.getByRole("button", { name: /start recording/i }),
    );
    expect(
      await screen.findByRole("button", { name: /stop recording/i }),
    ).toBeInTheDocument();

    FakeMediaRecorder.instances[0].emitChunk("clip-bytes");
    await userEvent.click(screen.getByRole("button", { name: /stop recording/i }));

    await waitFor(() => expect(onRecorded).toHaveBeenCalledTimes(1));
    const file = onRecorded.mock.calls[0][0] as File;
    expect(file).toBeInstanceOf(File);
    expect(file.name).toMatch(/^recording-.*\.webm$/);
  });

  test("shows a preview player and can discard the clip", async () => {
    const onRecorded = vi.fn();
    renderRecorder({ onRecorded });

    await userEvent.click(
      screen.getByRole("button", { name: /start recording/i }),
    );
    await userEvent.click(
      await screen.findByRole("button", { name: /stop recording/i }),
    );

    expect(
      await screen.findByLabelText(/recording preview/i),
    ).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: /discard/i }));

    expect(screen.queryByLabelText(/recording preview/i)).not.toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /start recording/i }),
    ).toBeInTheDocument();
  });

  test("says so plainly when the browser cannot record", () => {
    removeRecorderSupport();
    renderRecorder();

    expect(screen.getByText(/cannot record audio/i)).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /start recording/i }),
    ).not.toBeInTheDocument();
  });

  test("surfaces a denied microphone as an alert", async () => {
    Object.defineProperty(navigator, "mediaDevices", {
      configurable: true,
      value: {
        getUserMedia: async () =>
          Promise.reject(
            Object.assign(new Error("denied"), { name: "NotAllowedError" }),
          ),
      },
    });
    renderRecorder();

    await userEvent.click(
      screen.getByRole("button", { name: /start recording/i }),
    );

    expect(await screen.findByRole("alert")).toHaveTextContent(
      /microphone permission was denied/i,
    );
  });
});

test("shows live transcription while the microphone is open", async () => {
  server.use(
    http.post(`${BASE}/v1/transcriptions/preview`, () =>
      HttpResponse.json({
        text: "text heard so far",
        language: "en",
        segments: [],
        timestamp_granularity: "segment",
        audio_duration_s: 5,
        processing_duration_s: 0.3,
        provider: "faster-whisper",
        model: "large-v3-turbo",
        model_version: null,
        is_final: false,
      }),
    ),
  );

  renderRecorder();
  await userEvent.click(
    screen.getByRole("button", { name: /start recording/i }),
  );

  const panel = await screen.findByTestId("live-panel");
  expect(panel).toHaveTextContent(/listening/i);

  // The recorder hands the hook its audio; the preview then fires on its
  // interval (default 4 s), so give it room.
  FakeMediaRecorder.instances[0].emitChunk("heard-bytes");
  await waitFor(
    () => expect(screen.getByTestId("live-panel")).toHaveTextContent(/text heard so far/i),
    { timeout: 12000 },
  );
  expect(screen.getByTestId("live-panel")).toHaveTextContent(/faster-whisper/);
});

test("a broken preview never stops the recording", async () => {
  server.use(
    http.post(`${BASE}/v1/transcriptions/preview`, () =>
      HttpResponse.json(
        { error: { code: "PROVIDER_ERROR", message: "gpu busy" } },
        { status: 502 },
      ),
    ),
  );

  renderRecorder();
  await userEvent.click(
    screen.getByRole("button", { name: /start recording/i }),
  );
  FakeMediaRecorder.instances[0].emitChunk("heard-bytes");

  await waitFor(
    () => expect(screen.getByTestId("live-panel")).toHaveTextContent(/live preview unavailable/i),
    { timeout: 12000 },
  );
  // Still recording, and the take can still be finished.
  expect(
    screen.getByRole("button", { name: /stop recording/i }),
  ).toBeInTheDocument();
});

describe("Recorder streaming panel", () => {
  const READY = {
    type: "ready",
    provider: "faster-whisper",
    model: "large-v3-turbo",
    session_id: "s1",
    max_duration_s: 300,
    partial_interval_s: 1,
    sample_rate: 16000,
    input_format: "container",
  };

  test("shows closed text and the still-open utterance separately", async () => {
    FakeWebSocket.reset();
    renderRecorder({}, { streaming: true });

    await userEvent.click(
      screen.getByRole("button", { name: /start recording/i }),
    );
    const socket = FakeWebSocket.latest;
    act(() => socket.accept());
    act(() => socket.deliver(READY));

    act(() =>
      socket.deliver({
        type: "final",
        text: "hello world",
        segments: [],
        window: { start_s: 0, end_s: 1 },
      }),
    );
    expect(await screen.findByText(/hello world/)).toBeInTheDocument();

    act(() =>
      socket.deliver({
        type: "partial",
        text: "and then some",
        segments: [],
        window: { start_s: 1, end_s: 2 },
        is_final: false,
      }),
    );
    const partial = await screen.findByTestId("live-partial");
    expect(partial).toHaveTextContent("and then some");
    expect(screen.getByTestId("live-panel")).toHaveAttribute(
      "data-transport",
      "stream",
    );
  });
});

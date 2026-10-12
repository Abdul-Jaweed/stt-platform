/**
 * The streaming transport (T6).
 *
 * What matters here is not that frames are parsed, but the promises the panel
 * makes: text only ever grows, bytes are sent exactly once and in order, a
 * transport that cannot work is reported as such (so the caller may fall back
 * to the HTTP preview), and asking to stop still delivers the last utterance.
 *
 * Real timers throughout: FileReader (the Blob round trip) never fires its
 * callbacks under fake timers.
 */
import { act, renderHook, waitFor } from "@testing-library/react";

import { FakeWebSocket } from "../test/webSocket";
import { useLiveStream } from "./useLiveStream";

const URL_ = "ws://127.0.0.1:8200/v1/transcriptions/stream";
const READY = {
  type: "ready" as const,
  provider: "faster-whisper",
  model: "large-v3-turbo",
  session_id: "s1",
  max_duration_s: 300,
  partial_interval_s: 1,
  sample_rate: 16000,
  input_format: "container" as const,
};

function openSocket(url: string): WebSocket {
  return new FakeWebSocket(url) as unknown as WebSocket;
}

function setup(overrides: { language?: string; failToOpen?: boolean } = {}) {
  FakeWebSocket.reset();
  const chunks: Blob[] = [];
  const readChunks = (index: number) => chunks.slice(index);
  const view = renderHook(
    ({ active }: { active: boolean }) =>
      useLiveStream({
        url: URL_,
        active,
        readChunks,
        language: overrides.language ?? "en",
        openSocket: overrides.failToOpen
          ? () => {
              throw new Error("WebSocket is not available here");
            }
          : openSocket,
      }),
    { initialProps: { active: true } },
  );
  return { ...view, chunks };
}

test("sends a start frame, then turns the server's frames into state", () => {
  const { result } = setup();
  const socket = FakeWebSocket.latest;
  expect(socket.url).toBe(URL_);

  act(() => socket.accept());
  expect(socket.controlSent()).toEqual([
    { type: "start", language: "en", input_format: "container" },
  ]);

  act(() => socket.deliver(READY));
  expect(result.current.status).toBe("live");
  expect(result.current.provider).toBe("faster-whisper");
  expect(result.current.model).toBe("large-v3-turbo");

  act(() =>
    socket.deliver({
      type: "partial",
      text: "hello",
      segments: [],
      window: { start_s: 0, end_s: 1 },
      is_final: false,
    }),
  );
  expect(result.current.partial).toBe("hello");
  expect(result.current.text).toBe("");

  act(() =>
    socket.deliver({
      type: "final",
      text: "hello world",
      segments: [],
      window: { start_s: 0, end_s: 2 },
    }),
  );
  expect(result.current.text).toBe("hello world");
  expect(result.current.partial).toBe("");
  expect(result.current.finals).toBe(1);

  act(() => socket.deliver({ type: "done", reason: "stop", duration_s: 2, text: "hello world" }));
  expect(result.current.durationS).toBe(2);
});

test("sends each chunk of audio once, in order", async () => {
  const { chunks } = setup();
  const socket = FakeWebSocket.latest;
  act(() => socket.accept());

  chunks.push(new Blob([new Uint8Array([1, 2, 3])]), new Blob([new Uint8Array([4, 5])]));
  await waitFor(() => expect(socket.binarySent()).toHaveLength(2));
  expect([...socket.binarySent()[0]]).toEqual([1, 2, 3]);
  expect([...socket.binarySent()[1]]).toEqual([4, 5]);

  // Several drain ticks later, nothing was re-sent.
  await new Promise((resolve) => setTimeout(resolve, 400));
  expect(socket.binarySent()).toHaveLength(2);
});

test("a socket that never answers is 'unsupported', so the caller may fall back", () => {
  const { result } = setup();
  act(() => FakeWebSocket.latest.fail());
  expect(result.current.status).toBe("unsupported");
});

test("a socket that cannot even be constructed is 'unsupported'", () => {
  const { result } = setup({ failToOpen: true });
  expect(result.current.status).toBe("unsupported");
});

test("a failure after text has arrived is an error, not a quiet fallback", () => {
  const { result } = setup();
  const socket = FakeWebSocket.latest;
  act(() => socket.accept());
  act(() => socket.deliver(READY));
  act(() =>
    socket.deliver({
      type: "final",
      text: "first words",
      segments: [],
      window: { start_s: 0, end_s: 1 },
    }),
  );

  act(() => socket.fail());
  expect(result.current.status).toBe("live");
  expect(result.current.text).toBe("first words");
  expect(result.current.error).toMatch(/connection/i);
});

test("stopping asks the server for the last words instead of dropping them", () => {
  const { result, rerender } = setup();
  const socket = FakeWebSocket.latest;
  act(() => socket.accept());
  act(() => socket.deliver(READY));

  rerender({ active: false });
  expect(socket.controlSent().filter((frame) => frame.type === "stop")).toHaveLength(1);

  // The flush the client asked for still lands.
  act(() =>
    socket.deliver({
      type: "final",
      text: "the last words",
      segments: [],
      window: { start_s: 0, end_s: 3 },
    }),
  );
  expect(result.current.text).toBe("the last words");
});

test("a capped session says so and keeps its text", () => {
  const { result } = setup();
  const socket = FakeWebSocket.latest;
  act(() => socket.accept());
  act(() => socket.deliver(READY));
  act(() =>
    socket.deliver({
      type: "final",
      text: "long take",
      segments: [],
      window: { start_s: 0, end_s: 300 },
    }),
  );

  act(() => socket.deliver({ type: "capped", max_duration_s: 300 }));
  expect(result.current.capped).toBe(true);
  expect(result.current.text).toBe("long take");
});

test("a fatal server error is surfaced with its code", () => {
  const { result } = setup();
  const socket = FakeWebSocket.latest;
  act(() => socket.accept());
  act(() => socket.deliver(READY));

  act(() =>
    socket.deliver({
      type: "error",
      code: "STREAM_BUSY",
      message: "this server is already streaming 1 session(s)",
      fatal: true,
    }),
  );
  expect(result.current.status).toBe("error");
  expect(result.current.error).toContain("STREAM_BUSY");
});

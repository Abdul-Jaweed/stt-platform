/**
 * The fallback policy (T6): stream first, HTTP preview only when a socket
 * cannot be opened at all -- never on top of text the user is already
 * reading.
 */
import { act, renderHook, waitFor } from "@testing-library/react";

import { FakeWebSocket } from "../test/webSocket";
import { useLiveTranscript } from "./useLiveTranscript";

const STREAM_URL = "ws://127.0.0.1:8200/v1/transcriptions/stream";
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

function makeClient(previewText = "text from the preview") {
  return {
    streamUrl: () => STREAM_URL,
    previewTranscription: vi.fn(async () => ({
      text: previewText,
      provider: "faster-whisper",
    })),
  };
}

function render(live: {
  client: ReturnType<typeof makeClient>;
  openSocket?: (url: string) => WebSocket;
  getBlob?: () => Blob | null;
}) {
  return renderHook(() =>
    useLiveTranscript({
      client: live.client,
      active: true,
      seconds: 5,
      getBlob: live.getBlob ?? (() => null),
      readChunks: () => [],
      language: "en",
      openSocket: live.openSocket ?? openSocket,
    }),
  );
}

test("the stream answers when it can, and the preview is never called", () => {
  FakeWebSocket.reset();
  const client = makeClient();
  const { result } = render({ client });
  const socket = FakeWebSocket.latest;

  act(() => socket.accept());
  act(() => socket.deliver(READY));
  expect(result.current.transport).toBe("stream");
  expect(result.current.status).toBe("live");

  act(() =>
    socket.deliver({
      type: "final",
      text: "hello world",
      segments: [],
      window: { start_s: 0, end_s: 1 },
    }),
  );
  act(() =>
    socket.deliver({
      type: "partial",
      text: "and then",
      segments: [],
      window: { start_s: 1, end_s: 2 },
      is_final: false,
    }),
  );

  expect(result.current.text).toBe("hello world");
  expect(result.current.partial).toBe("and then");
  expect(client.previewTranscription).not.toHaveBeenCalled();
});

test("falls back to the HTTP preview when no socket can be opened", async () => {
  FakeWebSocket.reset();
  const client = makeClient("preview text");
  const { result } = render(
    {
      client,
      openSocket: () => {
        throw new Error("WebSocket is not available here");
      },
      getBlob: () => new Blob([new Uint8Array([1, 2, 3])], { type: "audio/webm" }),
    },
  );

  await waitFor(() => expect(result.current.transport).toBe("preview"));
  await waitFor(() => expect(result.current.text).toBe("preview text"));
  expect(result.current.partial).toBe("");
  expect(client.previewTranscription).toHaveBeenCalled();
});

test("a stream failure after text arrived is never replaced by the preview", () => {
  FakeWebSocket.reset();
  const client = makeClient("preview text");
  const { result } = render({ client });
  const socket = FakeWebSocket.latest;

  act(() => socket.accept());
  act(() => socket.deliver(READY));
  act(() =>
    socket.deliver({
      type: "final",
      text: "live text",
      segments: [],
      window: { start_s: 0, end_s: 1 },
    }),
  );
  act(() => socket.fail());

  expect(result.current.transport).toBe("stream");
  expect(result.current.text).toBe("live text");
  expect(result.current.error).toMatch(/connection/i);
  expect(client.previewTranscription).not.toHaveBeenCalled();
});

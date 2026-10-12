/**
 * Streaming transcription over the WebSocket (T6).
 *
 * The recorder hands over audio chunks as they are produced; the server
 * answers with `partial` frames while an utterance is open and a `final` when
 * it closes. Four promises this hook makes to the panel:
 *
 * 1. **Final text only ever grows.** A partial can be revised; a final cannot,
 *    because the server decoded that window once. So the panel shows the
 *    closed text in normal weight and the open utterance lighter.
 * 2. **Every chunk is sent exactly once, in order.** Chunk boundaries are
 *    byte-exact (concatenated chunks are the recording), so a re-send would
 *    corrupt the stream, and a re-order would too.
 * 3. **A transport that cannot work says so.** `unsupported` means nothing
 *    ever arrived from the server, and the caller may fall back to the HTTP
 *    preview. A failure *after* text arrived is an `error`: falling back then
 *    would silently replace what the user is reading.
 * 4. **Stopping still delivers the last words.** It asks the server to flush
 *    the open utterance and waits for the answer instead of dropping the
 *    socket (the recording itself never depends on any of this).
 */
import { useEffect, useRef, useState } from "react";

import type { StreamFrame } from "../api/types";
import { blobBytes } from "../lib/blobBytes";

export type LiveStreamStatus =
  | "idle"
  | "connecting"
  | "live"
  | "error"
  | "unsupported";

export interface LiveStreamState {
  status: LiveStreamStatus;
  /** Text of the utterances the server has closed. */
  text: string;
  /** The utterance still open: possibly revised, never shown as final. */
  partial: string;
  provider: string | null;
  model: string | null;
  durationS: number;
  error: string | null;
  capped: boolean;
  /** How many utterances the server has closed so far. */
  finals: number;
}

const EMPTY: LiveStreamState = {
  status: "idle",
  text: "",
  partial: "",
  provider: null,
  model: null,
  durationS: 0,
  error: null,
  capped: false,
  finals: 0,
};

export interface UseLiveStreamOptions {
  url: string;
  /** True while the recorder is capturing. */
  active: boolean;
  /** Audio chunks from `index` on; the hook only ever asks for new ones. */
  readChunks: (index: number) => Blob[];
  language?: string;
  /** How often new chunks are looked for and sent. */
  drainMs?: number;
  /** Test seam: how the socket is built. */
  openSocket?: (url: string) => WebSocket;
}

/** How long a stopped stream may take to say its last word before closing. */
const GRACE_MS = 5000;

export function useLiveStream({
  url,
  active,
  readChunks,
  language,
  drainMs = 150,
  openSocket,
}: UseLiveStreamOptions): LiveStreamState {
  const [state, setState] = useState<LiveStreamState>(EMPTY);

  const readChunksRef = useRef(readChunks);
  const languageRef = useRef(language);
  const openSocketRef = useRef(openSocket);
  readChunksRef.current = readChunks;
  languageRef.current = language;
  openSocketRef.current = openSocket;

  useEffect(() => {
    if (!active) return;
    if (typeof WebSocket === "undefined" && !openSocketRef.current) {
      setState({ ...EMPTY, status: "unsupported" });
      return;
    }

    setState({ ...EMPTY, status: "connecting" });
    let socket: WebSocket;
    try {
      const build = openSocketRef.current;
      socket = build ? build(url) : new WebSocket(url);
    } catch {
      setState({ ...EMPTY, status: "unsupported" });
      return;
    }

    let cursor = 0;
    let heardFromServer = false;
    let finished = false;
    let disposed = false;
    let sending = false;

    const sendControl = (payload: Record<string, unknown>) => {
      if (socket.readyState === WebSocket.OPEN) {
        socket.send(JSON.stringify(payload));
      }
    };

    const drain = async () => {
      if (sending || socket.readyState !== WebSocket.OPEN) return;
      sending = true;
      try {
        for (const chunk of readChunksRef.current(cursor)) {
          if (socket.readyState !== WebSocket.OPEN) return;
          const bytes = await blobBytes(chunk);
          if (socket.readyState !== WebSocket.OPEN) return;
          socket.send(bytes);
          cursor += 1;
        }
      } catch {
        // An unreadable chunk is skipped and counted: the recording is
        // untouched and still uploadable, so this must not wedge the stream.
        cursor += 1;
      } finally {
        sending = false;
      }
    };

    const timer = window.setInterval(() => void drain(), drainMs);

    socket.onopen = () => {
      sendControl({
        type: "start",
        language: languageRef.current || null,
        input_format: "container",
      });
    };

    socket.onmessage = (event) => {
      if (disposed) return;
      let frame: StreamFrame;
      try {
        frame = JSON.parse(String(event.data)) as StreamFrame;
      } catch {
        return;
      }
      heardFromServer = true;
      if (frame.type === "done") finished = true;
      setState((prev) => reduce(prev, frame));
    };

    // A close always follows an error; the verdict belongs in one place.
    socket.onerror = () => undefined;

    socket.onclose = (event) => {
      if (disposed) return;
      window.clearInterval(timer);
      if (!heardFromServer) {
        setState({ ...EMPTY, status: "unsupported" });
        return;
      }
      if (finished || event.code === 1000) return;
      setState((prev) => ({
        ...prev,
        error: prev.error ?? `the live connection dropped (code ${event.code})`,
      }));
    };

    return () => {
      // Do not close: ask for the open utterance, then let the server finish
      // (it closes the socket after `done`). A dead server cannot hold the
      // socket open past the grace period.
      sendControl({ type: "stop" });
      window.setTimeout(() => {
        disposed = true;
        window.clearInterval(timer);
        socket.close();
      }, GRACE_MS);
    };
  }, [active, drainMs, url]);

  return state;
}

function reduce(prev: LiveStreamState, frame: StreamFrame): LiveStreamState {
  switch (frame.type) {
    case "ready":
      return {
        ...prev,
        status: "live",
        provider: frame.provider,
        model: frame.model,
      };
    case "partial":
      return { ...prev, status: "live", partial: frame.text };
    case "final": {
      const text = frame.text
        ? [prev.text, frame.text].filter(Boolean).join(" ")
        : prev.text;
      return { ...prev, status: "live", text, partial: "", finals: prev.finals + 1 };
    }
    case "capped":
      return { ...prev, capped: true };
    case "error": {
      const message = `${frame.code}: ${frame.message}`;
      if (frame.fatal) {
        return { ...prev, status: prev.text ? "live" : "error", error: message };
      }
      return { ...prev, error: message };
    }
    case "done":
      return {
        ...prev,
        text: frame.text || prev.text,
        partial: "",
        durationS: frame.duration_s,
      };
    default:
      return prev;
  }
}

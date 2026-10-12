/**
 * One live-text state for the panel, whichever transport can serve it (T6).
 *
 * The WebSocket stream is the good path: partials while the utterance is
 * open, finals that never change, no re-transcription. The HTTP preview is
 * the fallback for a browser or a proxy where a socket cannot be opened at
 * all -- it never fires once the stream has said anything, because replacing
 * text the user is reading with a differently-shaped answer would be worse
 * than a slower one.
 */
import { useLivePreview } from "./useLivePreview";
import { useLiveStream } from "./useLiveStream";

export interface LiveTranscriptState {
  status: "idle" | "waiting" | "live" | "error";
  /** Text the server has finalised. */
  text: string;
  /** The utterance still open (stream only; the preview has no partials). */
  partial: string;
  provider: string | null;
  error: string | null;
  capped: boolean;
  /** Which transport is actually answering. */
  transport: "stream" | "preview" | null;
}

export interface UseLiveTranscriptOptions {
  client: {
    streamUrl: () => string;
    previewTranscription: (input: {
      blob: Blob;
      filename?: string;
      language?: string;
      signal?: AbortSignal;
    }) => Promise<{
      text: string;
      provider: string;
      error?: never;
    }>;
  };
  active: boolean;
  seconds: number;
  getBlob: () => Blob | null;
  readChunks: (index: number) => Blob[];
  language?: string;
  /** Test seam, passed through to the stream. */
  openSocket?: (url: string) => WebSocket;
}

export function useLiveTranscript({
  client,
  active,
  seconds,
  getBlob,
  readChunks,
  language,
  openSocket,
}: UseLiveTranscriptOptions): LiveTranscriptState {
  const stream = useLiveStream({
    url: client.streamUrl(),
    active,
    readChunks,
    language,
    openSocket,
  });

  const preview = useLivePreview({
    // The fallback only runs when the socket is out of the picture.
    client: client as unknown as Parameters<typeof useLivePreview>[0]["client"],
    active: active && stream.status === "unsupported",
    seconds,
    getBlob,
    language,
  });

  if (stream.status !== "unsupported") {
    return {
      status: stream.status === "connecting" ? "waiting" : stream.status,
      text: stream.text,
      partial: stream.partial,
      provider: stream.provider,
      error: stream.error,
      capped: stream.capped,
      transport: "stream",
    };
  }

  return {
    status: preview.status,
    text: preview.text,
    partial: "",
    provider: preview.provider,
    error: preview.error,
    capped: preview.capped,
    transport: "preview",
  };
}

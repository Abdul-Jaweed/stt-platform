/**
 * Live transcription while recording.
 *
 * Every tick the hook sends the audio captured *so far* and shows the text
 * that comes back. Three properties matter more than speed:
 *
 * 1. **It never disturbs the recording.** A failed or slow preview is a
 *    message in the panel; the microphone keeps running.
 * 2. **It never stacks requests.** A slow decode on a long take would
 *    otherwise queue work the GPU cannot retire.
 * 3. **It is honest about being a preview.** `is_final` stays false, the
 *    provider is the local one, and the cap stops the asking.
 */
import { useEffect, useRef, useState } from "react";

import type { ApiClient } from "../api/client";
import type { TranscriptSegment } from "../api/types";

export interface LivePreviewState {
  status: "idle" | "waiting" | "live" | "error";
  text: string;
  segments: TranscriptSegment[];
  provider: string | null;
  model: string | null;
  durationS: number;
  error: string | null;
  /** True once the take is longer than the preview cap: the job takes over. */
  capped: boolean;
}

const EMPTY: LivePreviewState = {
  status: "idle",
  text: "",
  segments: [],
  provider: null,
  model: null,
  durationS: 0,
  error: null,
  capped: false,
};

export interface UseLivePreviewOptions {
  client: ApiClient;
  /** True while the recorder is capturing. */
  active: boolean;
  /** Elapsed recording time, in seconds. */
  seconds: number;
  /** The audio captured so far, or null when there is nothing yet. */
  getBlob: () => Blob | null;
  language?: string;
  /** How often to ask. Re-transcription covers the whole take each time. */
  intervalMs?: number;
  /** Below this many seconds there is nothing useful to send. */
  minSeconds?: number;
  /** Above this many seconds the preview stops; the full job takes over. */
  maxSeconds?: number;
}

export function useLivePreview({
  client,
  active,
  seconds,
  getBlob,
  language,
  intervalMs = 4000,
  minSeconds = 2,
  maxSeconds = 120,
}: UseLivePreviewOptions): LivePreviewState {
  const [state, setState] = useState<LivePreviewState>(EMPTY);

  // Refs so the interval is not re-created on every render.
  const secondsRef = useRef(seconds);
  const activeRef = useRef(active);
  const getBlobRef = useRef(getBlob);
  const languageRef = useRef(language);
  const inFlightRef = useRef(false);
  const generationRef = useRef(0);

  secondsRef.current = seconds;
  activeRef.current = active;
  getBlobRef.current = getBlob;
  languageRef.current = language;

  useEffect(() => {
    if (!active) return;

    // A new take starts from a clean panel.
    setState({ ...EMPTY, status: "waiting" });
    const generation = ++generationRef.current;
    const controller = new AbortController();

    const tick = async () => {
      if (inFlightRef.current) return;
      if (secondsRef.current < minSeconds) {
        setState((prev) => (prev.status === "live" ? prev : { ...prev, status: "waiting" }));
        return;
      }
      if (secondsRef.current > maxSeconds) {
        setState((prev) => ({ ...prev, capped: true }));
        return;
      }
      const blob = getBlobRef.current();
      if (!blob || blob.size === 0) return;

      inFlightRef.current = true;
      try {
        const preview = await client.previewTranscription({
          blob,
          filename: `preview.${blob.type.includes("ogg") ? "ogg" : "webm"}`,
          language: languageRef.current,
          signal: controller.signal,
        });
        if (generationRef.current !== generation) return;
        setState({
          status: "live",
          text: preview.text,
          segments: preview.segments ?? [],
          provider: preview.provider,
          model: preview.model,
          durationS: preview.audio_duration_s,
          error: null,
          capped: false,
        });
      } catch (error) {
        if (generationRef.current !== generation) return;
        if ((error as { name?: string } | null)?.name === "AbortError") return;
        const message =
          error instanceof Error ? error.message : String(error);
        setState((prev) => ({
          ...prev,
          status: prev.text ? "live" : "error",
          error: message,
        }));
      } finally {
        inFlightRef.current = false;
      }
    };

    const timer = window.setInterval(() => void tick(), intervalMs);
    void tick(); // do not wait a full interval before the first attempt

    return () => {
      window.clearInterval(timer);
      controller.abort();
      inFlightRef.current = false;
    };
  }, [active, client, intervalMs, maxSeconds, minSeconds]);

  return state;
}

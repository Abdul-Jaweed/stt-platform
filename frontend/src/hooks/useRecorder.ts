/**
 * Browser audio recording for the upload page.
 *
 * Wraps `MediaRecorder` with the states the UI needs (requesting → recording →
 * ready), a ticking timer, and a best-effort input level. Two rules this hook
 * takes seriously:
 *
 * 1. **The microphone is never left hot.** Every path out of `recording` —
 *    stop, reset, unmount, a recorder error — stops the stream's tracks.
 * 2. **A failure is a message, not a crash.** Denied permission, no device and
 *    an unsupported browser each produce readable text for the user.
 */
import { useCallback, useEffect, useRef, useState } from "react";

export type RecorderStatus =
  | "idle"
  | "requesting"
  | "recording"
  | "ready"
  | "error";

export interface RecordedClip {
  file: File;
  /** Object URL for the preview player; revoked when the clip is replaced. */
  url: string;
  seconds: number;
}

/** In preference order; the first the browser admits to supporting wins. */
export const PREFERRED_MIME_TYPES = [
  "audio/webm;codecs=opus",
  "audio/webm",
  "audio/ogg;codecs=opus",
  "audio/mp4",
] as const;

export function pickMimeType(): string | null {
  if (typeof MediaRecorder === "undefined") return null;
  if (typeof MediaRecorder.isTypeSupported !== "function") return null;
  return PREFERRED_MIME_TYPES.find((type) => MediaRecorder.isTypeSupported(type)) ?? null;
}

export function extensionFor(mimeType: string | null): string {
  if (!mimeType) return "webm";
  if (mimeType.includes("ogg")) return "ogg";
  if (mimeType.includes("mp4")) return "m4a";
  return "webm";
}

export function readableRecorderError(error: unknown): string {
  const name = (error as { name?: string } | null)?.name ?? "";
  switch (name) {
    case "NotAllowedError":
    case "SecurityError":
      return "Microphone permission was denied. Allow access in the browser, then try again.";
    case "NotFoundError":
    case "OverconstrainedError":
      return "No microphone was found on this device.";
    case "NotReadableError":
      return "The microphone is in use by another application.";
    default:
      return `Could not start recording: ${
        error instanceof Error ? error.message : String(error)
      }`;
  }
}

export function useRecorder() {
  const [status, setStatus] = useState<RecorderStatus>("idle");
  const [seconds, setSeconds] = useState(0);
  const [level, setLevel] = useState(0);
  const [clip, setClip] = useState<RecordedClip | null>(null);
  const [error, setError] = useState<string | null>(null);

  const recorderRef = useRef<MediaRecorder | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const chunksRef = useRef<Blob[]>([]);
  const timerRef = useRef<number | null>(null);
  const frameRef = useRef<number | null>(null);
  const meterRef = useRef<{ context: AudioContext } | null>(null);
  const secondsRef = useRef(0);
  const urlRef = useRef<string | null>(null);

  const supported =
    typeof MediaRecorder !== "undefined" &&
    typeof navigator !== "undefined" &&
    Boolean(navigator.mediaDevices?.getUserMedia);

  const clearTimer = useCallback(() => {
    if (timerRef.current !== null) {
      window.clearInterval(timerRef.current);
      timerRef.current = null;
    }
  }, []);

  const stopMeter = useCallback(() => {
    if (frameRef.current !== null) {
      cancelAnimationFrame(frameRef.current);
      frameRef.current = null;
    }
    const meter = meterRef.current;
    meterRef.current = null;
    if (meter) {
      void meter.context.close().catch(() => undefined);
    }
    setLevel(0);
  }, []);

  const releaseMicrophone = useCallback(() => {
    streamRef.current?.getTracks().forEach((track) => track.stop());
    streamRef.current = null;
  }, []);

  const startMeter = useCallback((stream: MediaStream) => {
    // Best effort: no WebAudio (jsdom, locked-down browsers) simply means no
    // level meter, never a broken recording.
    try {
      const Ctor =
        window.AudioContext ??
        (window as unknown as { webkitAudioContext?: typeof AudioContext })
          .webkitAudioContext;
      if (!Ctor) return;
      const context = new Ctor();
      const source = context.createMediaStreamSource(stream);
      const analyser = context.createAnalyser();
      analyser.fftSize = 512;
      source.connect(analyser);
      meterRef.current = { context };
      const samples = new Uint8Array(analyser.fftSize);
      const tick = () => {
        analyser.getByteTimeDomainData(samples);
        let sum = 0;
        for (const value of samples) {
          const centred = (value - 128) / 128;
          sum += centred * centred;
        }
        const rms = Math.sqrt(sum / samples.length);
        setLevel(Math.min(1, rms * 4));
        frameRef.current = requestAnimationFrame(tick);
      };
      frameRef.current = requestAnimationFrame(tick);
    } catch {
      meterRef.current = null;
    }
  }, []);

  const reset = useCallback(() => {
    clearTimer();
    stopMeter();
    const recorder = recorderRef.current;
    if (recorder && recorder.state === "recording") {
      recorder.onstop = null;
      recorder.stop();
    }
    recorderRef.current = null;
    releaseMicrophone();
    chunksRef.current = [];
    secondsRef.current = 0;
    if (urlRef.current) {
      URL.revokeObjectURL(urlRef.current);
      urlRef.current = null;
    }
    setSeconds(0);
    setClip(null);
    setError(null);
    setStatus("idle");
  }, [clearTimer, releaseMicrophone, stopMeter]);

  const start = useCallback(async () => {
    if (!supported) {
      setStatus("error");
      setError(
        "This browser cannot record audio (MediaRecorder is unavailable). Upload a file instead.",
      );
      return;
    }
    setError(null);
    setStatus("requesting");
    setSeconds(0);
    secondsRef.current = 0;
    chunksRef.current = [];

    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      streamRef.current = stream;

      const mimeType = pickMimeType();
      const recorder = new MediaRecorder(
        stream,
        mimeType ? { mimeType } : undefined,
      );
      recorderRef.current = recorder;

      recorder.ondataavailable = (event) => {
        if (event.data && event.data.size > 0) {
          chunksRef.current.push(event.data);
        }
      };

      recorder.onstop = () => {
        const type = recorder.mimeType || mimeType || "audio/webm";
        const blob = new Blob(chunksRef.current, { type });
        const file = new File(
          [blob],
          `recording-${Date.now()}.${extensionFor(type)}`,
          { type },
        );
        if (urlRef.current) URL.revokeObjectURL(urlRef.current);
        const url = URL.createObjectURL(file);
        urlRef.current = url;
        setClip({ file, url, seconds: secondsRef.current });
        setStatus("ready");
        clearTimer();
        stopMeter();
        releaseMicrophone();
      };

      recorder.onerror = () => {
        clearTimer();
        stopMeter();
        releaseMicrophone();
        setStatus("error");
        setError("Recording failed in the browser. Try again or upload a file.");
      };

      recorder.start(250);
      setStatus("recording");
      timerRef.current = window.setInterval(() => {
        secondsRef.current += 1;
        setSeconds(secondsRef.current);
      }, 1000);
      startMeter(stream);
    } catch (err) {
      releaseMicrophone();
      setStatus("error");
      setError(readableRecorderError(err));
    }
  }, [
    clearTimer,
    releaseMicrophone,
    startMeter,
    stopMeter,
    supported,
  ]);

  const stop = useCallback(() => {
    const recorder = recorderRef.current;
    if (!recorder || recorder.state === "inactive") return;
    recorder.stop(); // onstop assembles the clip and releases the microphone
  }, []);

  useEffect(
    () => () => {
      clearTimer();
      stopMeter();
      const recorder = recorderRef.current;
      if (recorder && recorder.state === "recording") {
        recorder.onstop = null;
        recorder.stop();
      }
      releaseMicrophone();
      if (urlRef.current) URL.revokeObjectURL(urlRef.current);
    },
    [clearTimer, releaseMicrophone, stopMeter],
  );

  return {
    supported,
    status,
    seconds,
    level,
    clip,
    error,
    start,
    stop,
    reset,
  };
}

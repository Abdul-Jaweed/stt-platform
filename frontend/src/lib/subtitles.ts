import type { TranscriptResult } from "../api/types";

function pad(value: number, width: number): string {
  return String(value).padStart(width, "0");
}

function splitMs(seconds: number) {
  const totalMs = Math.max(0, Math.round(seconds * 1000));
  const ms = totalMs % 1000;
  const totalS = Math.floor(totalMs / 1000);
  return {
    h: Math.floor(totalS / 3600),
    m: Math.floor((totalS % 3600) / 60),
    s: totalS % 60,
    ms,
  };
}

/** HH:MM:SS,mmm (SRT convention). */
export function srtTimestamp(seconds: number): string {
  const { h, m, s, ms } = splitMs(seconds);
  return `${pad(h, 2)}:${pad(m, 2)}:${pad(s, 2)},${pad(ms, 3)}`;
}

/** HH:MM:SS.mmm (WebVTT convention). */
export function vttTimestamp(seconds: number): string {
  const { h, m, s, ms } = splitMs(seconds);
  return `${pad(h, 2)}:${pad(m, 2)}:${pad(s, 2)}.${pad(ms, 3)}`;
}

/** Exports use segment timings; word granularity is display-only. */
export function toPlainText(result: TranscriptResult): string {
  return result.segments.map((segment) => segment.text.trim()).join("\n");
}

export function toSrt(result: TranscriptResult): string {
  return result.segments
    .map(
      (segment, index) =>
        `${index + 1}\n` +
        `${srtTimestamp(segment.start)} --> ${srtTimestamp(segment.end)}\n` +
        `${segment.text.trim()}\n`,
    )
    .join("\n");
}

export function toVtt(result: TranscriptResult): string {
  return (
    "WEBVTT\n\n" +
    result.segments
      .map(
        (segment) =>
          `${vttTimestamp(segment.start)} --> ${vttTimestamp(segment.end)}\n` +
          `${segment.text.trim()}\n`,
      )
      .join("\n")
  );
}

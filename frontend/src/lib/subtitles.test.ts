import type { TranscriptResult } from "../api/types";
import { toPlainText, toSrt, toVtt } from "./subtitles";

const result: TranscriptResult = {
  job_id: "j-1",
  text: "Hello world Second line",
  segments: [
    { start: 0, end: 2.5, text: " Hello world " },
    { start: 2.5, end: 3661.234, text: "Second line" },
  ],
  timestamp_granularity: "segment",
  audio_duration_s: 3700,
  processing_duration_s: 10,
  provider: "faster-whisper",
};

test("srt uses segment timings with comma millis", () => {
  expect(toSrt(result)).toBe(
    "1\n00:00:00,000 --> 00:00:02,500\nHello world\n\n" +
      "2\n00:00:02,500 --> 01:01:01,234\nSecond line\n",
  );
});

test("vtt has the WEBVTT header and dot millis", () => {
  expect(toVtt(result)).toBe(
    "WEBVTT\n\n" +
      "00:00:00.000 --> 00:00:02.500\nHello world\n\n" +
      "00:00:02.500 --> 01:01:01.234\nSecond line\n",
  );
});

test("plain text joins trimmed segment texts", () => {
  expect(toPlainText(result)).toBe("Hello world\nSecond line");
});

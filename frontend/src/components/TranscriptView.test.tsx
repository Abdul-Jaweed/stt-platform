import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import type { TranscriptResult } from "../api/types";
import TranscriptView from "./TranscriptView";

function makeResult(
  granularity: "word" | "segment",
  withWords: boolean,
): TranscriptResult {
  return {
    job_id: "j-1",
    text: "Hello world Second line",
    segments: [
      {
        start: 0,
        end: 2.5,
        text: "Hello world",
        words: withWords
          ? [
              { start: 0, end: 1.2, text: "Hello" },
              { start: 1.2, end: 2.5, text: "world" },
            ]
          : null,
      },
      { start: 2.5, end: 4, text: "Second line" },
    ],
    timestamp_granularity: granularity,
    audio_duration_s: 4,
    processing_duration_s: 0.5,
    provider: "faster-whisper",
  };
}

test("word rows render only when granularity is word", () => {
  const { unmount } = render(
    <TranscriptView
      result={makeResult("word", true)}
      currentTime={0}
      onSeek={() => {}}
    />,
  );
  expect(screen.getAllByTestId("word-row")).toHaveLength(2);
  unmount();

  render(
    <TranscriptView
      result={makeResult("segment", true)}
      currentTime={0}
      onSeek={() => {}}
    />,
  );
  expect(screen.queryAllByTestId("word-row")).toHaveLength(0);
});

test("clicking a segment seeks to its start", async () => {
  const onSeek = vi.fn();
  render(
    <TranscriptView
      result={makeResult("segment", false)}
      currentTime={0}
      onSeek={onSeek}
    />,
  );

  await userEvent.click(screen.getByRole("button", { name: "0:02.500" }));

  expect(onSeek).toHaveBeenCalledWith(2.5);
});

test("the active segment follows playback time", () => {
  const result = makeResult("segment", false);
  const { container, rerender } = render(
    <TranscriptView result={result} currentTime={0} onSeek={() => {}} />,
  );

  const items = () => container.querySelectorAll("li.segment");
  expect(items()[0].className).toContain("active");
  expect(items()[1].className).not.toContain("active");

  rerender(
    <TranscriptView result={result} currentTime={3} onSeek={() => {}} />,
  );
  expect(items()[0].className).not.toContain("active");
  expect(items()[1].className).toContain("active");
});

test("a 500-segment transcript renders and stays interactive", async () => {
  const onSeek = vi.fn();
  const big: TranscriptResult = {
    ...makeResult("segment", false),
    segments: Array.from({ length: 500 }, (_, i) => ({
      start: i * 2,
      end: i * 2 + 2,
      text: `Segment number ${i}`,
    })),
  };

  const { container } = render(
    <TranscriptView result={big} currentTime={0} onSeek={onSeek} />,
  );

  expect(container.querySelectorAll("li.segment")).toHaveLength(500);
  await userEvent.click(screen.getByRole("button", { name: "0:10.000" }));
  expect(onSeek).toHaveBeenCalledWith(10);
});

import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, test, vi } from "vitest";

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

test("copies one segment's text without touching the others", async () => {
  const writeText = vi.fn().mockResolvedValue(undefined);
  Object.defineProperty(navigator, "clipboard", {
    configurable: true,
    value: { writeText },
  });

  render(
    <TranscriptView
      result={makeResult("word", true)}
      currentTime={0}
      onSeek={() => {}}
    />,
  );

  await userEvent.click(
    screen.getByRole("button", { name: /copy segment 2/i }),
  );

  expect(writeText).toHaveBeenCalledWith("Second line");
});

test("marks the active segment for the whole UI, not just the text", () => {
  render(
    <TranscriptView
      result={makeResult("segment", false)}
      currentTime={3}
      onSeek={() => {}}
    />,
  );

  const segments = document.querySelectorAll("li.segment");
  expect(segments[0].className).not.toContain("active");
  expect(segments[1].className).toContain("active");
});

test("the first word chip does not repeat the segment timestamp", () => {
  render(
    <TranscriptView
      result={makeResult("word", true)}
      currentTime={0}
      onSeek={() => {}}
    />,
  );

  const chips = screen.getAllByTestId("word-row");
  // The gutter already says 0:00.000 for this segment; the chip just says
  // the word, and keeps the timestamp in its title for hovering.
  expect(chips[0].textContent?.trim()).toBe("Hello");
  expect(chips[0].querySelector("button")).toHaveAttribute("title", "0:00.000");
  expect(chips[1].textContent).toContain("0:01.200");
});

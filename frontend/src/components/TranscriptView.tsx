import { useState } from "react";

import type { TranscriptResult } from "../api/types";
import { formatTimestamp } from "../lib/format";

interface Props {
  result: TranscriptResult;
  currentTime: number;
  onSeek: (seconds: number) => void;
}

function isActive(currentTime: number, start: number, end: number): boolean {
  return currentTime >= start && currentTime < end;
}

/**
 * The transcript itself: a time gutter, the segment text, and the word chips.
 *
 * Hierarchy is deliberate — the timestamp is the smallest thing you need and
 * lives in its own column, the text is the largest, and the word timings stay
 * quiet until you look for them. The active segment (and word) is marked on
 * the element, so playback state is visible without reading the text.
 */
export default function TranscriptView({ result, currentTime, onSeek }: Props) {
  const showWords = result.timestamp_granularity === "word";
  const [copiedIndex, setCopiedIndex] = useState<number | null>(null);

  const copySegment = async (index: number, text: string) => {
    try {
      await navigator.clipboard.writeText(text.trim());
      setCopiedIndex(index);
      window.setTimeout(
        () => setCopiedIndex((current) => (current === index ? null : current)),
        1600,
      );
    } catch {
      // A blocked clipboard is not worth an error banner; the export buttons
      // are the reliable path.
    }
  };

  return (
    <ol className="transcript">
      {result.segments.map((segment, index) => {
        const active = isActive(currentTime, segment.start, segment.end);
        return (
          <li
            key={index}
            className={active ? "segment active" : "segment"}
            data-segment-index={index}
          >
            <div className="segment-rail">
              <button
                type="button"
                className="segment-time"
                onClick={() => onSeek(segment.start)}
                title="Play from here"
              >
                {formatTimestamp(segment.start)}
              </button>
              <button
                type="button"
                className="segment-copy"
                aria-label={`Copy segment ${index + 1}`}
                title="Copy this segment"
                onClick={() => void copySegment(index, segment.text)}
              >
                {copiedIndex === index ? "copied" : "copy"}
              </button>
            </div>

            <div className="segment-body">
              <p className="segment-text">{segment.text.trim()}</p>
              {showWords && segment.words && segment.words.length > 0 && (
                <ul className="words">
                  {segment.words.map((word, wordIndex) => {
                    // The segment's own timestamp is already in the gutter;
                    // repeating it on the first chip is pure noise.
                    const clockIsRedundant =
                      wordIndex === 0 &&
                      Math.abs(word.start - segment.start) < 0.05;
                    return (
                    <li
                      key={wordIndex}
                      data-testid="word-row"
                      className={
                        isActive(currentTime, word.start, word.end)
                          ? "word active"
                          : "word"
                      }
                    >
                      <button
                        type="button"
                        className="word-time"
                        onClick={() => onSeek(word.start)}
                        title={formatTimestamp(word.start)}
                      >
                        {!clockIsRedundant && (
                          <>
                            <span className="word-clock">
                              {formatTimestamp(word.start)}
                            </span>{" "}
                          </>
                        )}
                        <span className="word-text">{word.text}</span>
                      </button>
                    </li>
                    );
                  })}
                </ul>
              )}
            </div>
          </li>
        );
      })}
    </ol>
  );
}

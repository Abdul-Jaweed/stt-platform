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

export default function TranscriptView({ result, currentTime, onSeek }: Props) {
  const showWords = result.timestamp_granularity === "word";

  return (
    <ol className="transcript">
      {result.segments.map((segment, index) => (
        <li
          key={index}
          className={
            isActive(currentTime, segment.start, segment.end)
              ? "segment active"
              : "segment"
          }
        >
          <button
            type="button"
            className="segment-time"
            onClick={() => onSeek(segment.start)}
          >
            {formatTimestamp(segment.start)}
          </button>
          <p className="segment-text">{segment.text.trim()}</p>
          {showWords && segment.words && segment.words.length > 0 && (
            <ul className="words">
              {segment.words.map((word, wordIndex) => (
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
                  >
                    {formatTimestamp(word.start)} {word.text}
                  </button>
                </li>
              ))}
            </ul>
          )}
        </li>
      ))}
    </ol>
  );
}

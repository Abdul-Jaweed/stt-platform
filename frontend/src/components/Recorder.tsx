/**
 * Recording panel for the upload page.
 *
 * Owns no submission logic: it produces a File and hands it to the page, so a
 * recorded clip travels the same upload path as a chosen file — same
 * retention, same playback, same errors.
 */
import { useEffect } from "react";

import { useRecorder } from "../hooks/useRecorder";
import { formatClock } from "../lib/format";

export default function Recorder({
  onRecorded,
  onDiscarded,
  disabled = false,
}: {
  onRecorded: (file: File) => void;
  onDiscarded?: () => void;
  disabled?: boolean;
}) {
  const { supported, status, seconds, level, clip, error, start, stop, reset } =
    useRecorder();

  useEffect(() => {
    if (clip) onRecorded(clip.file);
  }, [clip, onRecorded]);

  if (!supported) {
    return (
      <div className="recorder">
        <p className="hint">
          This browser cannot record audio (MediaRecorder is unavailable) —
          upload a file instead.
        </p>
      </div>
    );
  }

  const recording = status === "recording";

  return (
    <div className="recorder">
      <div className="recorder-controls">
        {!recording && !clip && (
          <button
            type="button"
            className="record-button"
            onClick={() => void start()}
            disabled={disabled || status === "requesting"}
          >
            <span className="record-dot" aria-hidden="true" />
            {status === "requesting" ? "Waiting for microphone…" : "Start recording"}
          </button>
        )}

        {recording && (
          <>
            <button
              type="button"
              className="record-button is-recording"
              onClick={stop}
              aria-label="Stop recording"
            >
              <span className="record-square" aria-hidden="true" />
              Stop recording
            </button>
            <span className="recorder-timer" role="timer">
              {formatClock(seconds)}
            </span>
            <span
              className="recorder-level"
              role="meter"
              aria-label="Microphone input level"
              aria-valuemin={0}
              aria-valuemax={100}
              aria-valuenow={Math.round(level * 100)}
            >
              <span
                className="recorder-level-fill"
                style={{ width: `${Math.max(2, Math.round(level * 100))}%` }}
              />
            </span>
          </>
        )}
      </div>

      {clip && (
        <div className="recorder-preview">
          {/* eslint-disable-next-line jsx-a11y/media-has-caption -- a recording has no captions yet */}
          <audio src={clip.url} controls aria-label="Recording preview" />
          <div className="recorder-preview-meta">
            <span className="hint">
              {formatClock(clip.seconds)} recorded — this is what will be
              transcribed.
            </span>
            <button
              type="button"
              className="ghost"
              onClick={() => {
                reset();
                onDiscarded?.();
              }}
            >
              Discard
            </button>
          </div>
        </div>
      )}

      {error && (
        <p role="alert" className="error">
          {error}
        </p>
      )}
    </div>
  );
}

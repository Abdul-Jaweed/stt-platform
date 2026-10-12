/**
 * Recording panel for the upload page.
 *
 * Owns no submission logic: it produces a File and hands it to the page, so a
 * recorded clip travels the same upload path as a chosen file — same
 * retention, same playback, same errors.
 */
import {
  ArrowCounterClockwise,
  Record,
  Stop,
} from "@phosphor-icons/react";
import { useEffect } from "react";

import { useApi } from "../api/context";
import { useLiveTranscript } from "../hooks/useLiveTranscript";
import { useRecorder } from "../hooks/useRecorder";
import { formatClock } from "../lib/format";

export default function Recorder({
  onRecorded,
  onDiscarded,
  language,
  disabled = false,
  openSocket,
}: {
  onRecorded: (file: File) => void;
  onDiscarded?: () => void;
  /** Language hint for the live transcript (the page owns the choice). */
  language?: string;
  disabled?: boolean;
  /** Test seam: how the streaming socket is built. */
  openSocket?: (url: string) => WebSocket;
}) {
  const api = useApi();
  const {
    supported,
    status,
    seconds,
    level,
    clip,
    error,
    getBlob,
    readChunks,
    start,
    stop,
    reset,
  } = useRecorder();

  // Live text while the microphone is open: a stream when the socket works,
  // the older chunked preview when it cannot be opened. It never touches the
  // recording itself and never persists anything.
  const live = useLiveTranscript({
    client: api,
    active: status === "recording",
    seconds,
    getBlob,
    readChunks,
    language,
    openSocket,
  });

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
            <Record size={15} weight="fill" aria-hidden="true" />
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
              <Stop size={15} weight="fill" aria-hidden="true" />
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

      {recording && (
        <div
          className="live-panel"
          data-testid="live-panel"
          data-transport={live.transport ?? "none"}
          aria-label="Live transcript preview"
        >
          <div className="live-head">
            <span
              className={live.status === "live" ? "live-dot is-live" : "live-dot"}
              aria-hidden="true"
            />
            <span className="live-title">Live transcript</span>
            {live.provider && (
              <span className="badge badge-neutral">{live.provider}</span>
            )}
            <span className="hint">
              runs on the local model — your chosen provider writes the final
              transcript
            </span>
          </div>

          {live.capped ? (
            <p className="hint">
              Preview stops after a couple of minutes — stop recording to
              transcribe the whole take.
            </p>
          ) : live.text || live.partial ? (
            <p className="live-text">
              {live.text}
              {/*
                The open utterance can still change, so it is set apart rather
                than run together with the words the server has already closed.
              */}
              {live.partial && (
                <span className="live-partial" data-testid="live-partial">
                  {live.text ? " " : ""}
                  {live.partial}
                </span>
              )}
            </p>
          ) : (
            <p className="hint live-waiting">Listening…</p>
          )}

          {live.error && (
            <p className="hint">live preview unavailable: {live.error}</p>
          )}
        </div>
      )}

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
              <ArrowCounterClockwise size={14} aria-hidden="true" />
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

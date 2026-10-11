import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";

import { useApi } from "../api/context";
import { ApiError } from "../api/errors";
import type { TranscriptResult } from "../api/types";
import TranscriptView from "../components/TranscriptView";
import { downloadText } from "../lib/download";
import { formatDuration } from "../lib/format";
import { getRetainedAudio } from "../lib/retainedAudio";
import { toPlainText, toSrt, toVtt } from "../lib/subtitles";

interface LoadError {
  code: string;
  message: string;
}

export default function TranscriptPage() {
  const { id } = useParams();
  const api = useApi();
  const jobId = id ?? "";

  const [result, setResult] = useState<TranscriptResult | null>(null);
  const [error, setError] = useState<LoadError | null>(null);
  const [loading, setLoading] = useState(true);
  const [currentTime, setCurrentTime] = useState(0);
  const [audioUrl, setAudioUrl] = useState<string | null>(null);
  const audioRef = useRef<HTMLAudioElement | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setResult(await api.getResult(jobId));
    } catch (err) {
      setError(
        err instanceof ApiError
          ? { code: err.code, message: err.message }
          : { code: "UNKNOWN_ERROR", message: String(err) },
      );
    } finally {
      setLoading(false);
    }
  }, [api, jobId]);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    const file = getRetainedAudio(jobId);
    if (!file) {
      setAudioUrl(null);
      return;
    }
    const url = URL.createObjectURL(file);
    setAudioUrl(url);
    return () => URL.revokeObjectURL(url);
  }, [jobId]);

  const onSeek = (seconds: number) => {
    setCurrentTime(seconds);
    const audio = audioRef.current;
    if (!audio) return;
    try {
      audio.currentTime = seconds;
    } catch {
      // jsdom / unsupported element
    }
    try {
      const playing = audio.play();
      if (playing && typeof playing.catch === "function") {
        void playing.catch(() => {});
      }
    } catch {
      // jsdom / autoplay policy
    }
  };

  if (loading && !result && !error) {
    return <p className="hint">Loading transcript…</p>;
  }

  if (error && !result) {
    return (
      <section className="page transcript-page">
        <h2>Transcript {jobId}</h2>
        <p role="alert" className="error">
          <strong>{error.code}</strong> {error.message}
        </p>
        <button type="button" onClick={() => void load()}>
          Retry
        </button>
        <p>
          <Link to={`/jobs/${jobId}`}>Back to job</Link>
        </p>
      </section>
    );
  }

  if (!result) return null;

  return (
    <section className="page transcript-page">
      <h2>Transcript {result.job_id}</h2>
      <p className="hint">
        {result.provider}
        {result.model ? ` — ${result.model}` : ""}
        {result.language ? ` — ${result.language}` : ""} — audio{" "}
        {formatDuration(result.audio_duration_s)}, processed in{" "}
        {formatDuration(result.processing_duration_s)}
      </p>

      {audioUrl ? (
        <audio
          ref={audioRef}
          controls
          src={audioUrl}
          onTimeUpdate={(event) =>
            setCurrentTime(event.currentTarget.currentTime)
          }
        />
      ) : (
        <p className="hint">
          Audio playback unavailable: the original file is not attached to
          this page (reload or a new tab loses it — the API serves no audio
          stream yet).
        </p>
      )}

      <div className="actions">
        <button
          type="button"
          onClick={() => downloadText(`${result.job_id}.srt`, toSrt(result))}
        >
          Download SRT
        </button>
        <button
          type="button"
          onClick={() => downloadText(`${result.job_id}.vtt`, toVtt(result))}
        >
          Download VTT
        </button>
        <button
          type="button"
          onClick={() => downloadText(`${result.job_id}.txt`, toPlainText(result))}
        >
          Download TXT
        </button>
        <Link to={`/jobs/${result.job_id}`}>Back to job</Link>
      </div>

      <TranscriptView
        result={result}
        currentTime={currentTime}
        onSeek={onSeek}
      />
    </section>
  );
}

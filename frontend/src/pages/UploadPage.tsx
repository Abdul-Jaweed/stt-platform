import {
  ArrowsClockwise,
  Microphone,
  UploadSimple,
  Waveform,
  X,
} from "@phosphor-icons/react";
import { useCallback, useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";

import { useApi } from "../api/context";
import { ApiError } from "../api/errors";
import type { ProviderInfo } from "../api/types";
import Recorder from "../components/Recorder";
import { formatBytes } from "../lib/format";
import { setRetainedAudio } from "../lib/retainedAudio";

interface SubmitError {
  code: string;
  message: string;
}

type Source = "file" | "record";

function newIdempotencyKey(): string {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) {
    return crypto.randomUUID();
  }
  return `key-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

export default function UploadPage() {
  const api = useApi();
  const navigate = useNavigate();

  const [providers, setProviders] = useState<ProviderInfo[] | null>(null);
  const [providersError, setProvidersError] = useState<string | null>(null);
  const [source, setSource] = useState<Source>("file");
  const [file, setFile] = useState<File | null>(null);
  const [origin, setOrigin] = useState<"upload" | "recording">("upload");
  const [dragging, setDragging] = useState(false);
  const [provider, setProvider] = useState("");
  const [language, setLanguage] = useState("en");
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<SubmitError | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  const loadProviders = useCallback(async () => {
    setProvidersError(null);
    try {
      const list = await api.getProviders();
      setProviders(list);
    } catch (err) {
      setProvidersError(err instanceof ApiError ? err.message : String(err));
    }
  }, [api]);

  useEffect(() => {
    void loadProviders();
  }, [loadProviders]);

  const selectFile = useCallback((next: File, from: "upload" | "recording") => {
    setFile(next);
    setOrigin(from);
    setSubmitError(null);
  }, []);

  const clearSelection = useCallback(() => {
    setFile(null);
    setOrigin("upload");
    if (inputRef.current) inputRef.current.value = "";
  }, []);

  const onRecorded = useCallback(
    (recorded: File) => selectFile(recorded, "recording"),
    [selectFile],
  );

  function onDrop(event: React.DragEvent<HTMLDivElement>) {
    event.preventDefault();
    setDragging(false);
    const dropped = event.dataTransfer?.files?.[0];
    if (dropped) {
      selectFile(dropped, "upload");
      setSource("file");
    }
  }

  async function onSubmit(event: React.FormEvent) {
    event.preventDefault();
    if (!file || submitting) return;
    setSubmitting(true);
    setSubmitError(null);
    try {
      const { job } = await api.createTranscription({
        file,
        provider: provider || undefined,
        language: language.trim() || undefined,
        idempotencyKey: newIdempotencyKey(),
      });
      setRetainedAudio(job.id, file);
      navigate(`/jobs/${job.id}`);
    } catch (err) {
      setSubmitError(
        err instanceof ApiError
          ? { code: err.code, message: err.message }
          : { code: "UNKNOWN_ERROR", message: String(err) },
      );
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <section className="page upload-page">
      <header className="page-head">
        <h2>New transcription</h2>
        <p className="hint">
          Upload a file, or record straight from your microphone — both take
          the same path through the platform.
        </p>
      </header>

      <form onSubmit={onSubmit}>
      <div className="tabs" role="tablist" aria-label="Audio source">
        <button
          type="button"
          role="tab"
          id="tab-file"
          aria-selected={source === "file"}
          aria-controls="panel-file"
          className={source === "file" ? "tab is-active" : "tab"}
          onClick={() => setSource("file")}
        >
          <UploadSimple size={16} aria-hidden="true" />
          Upload a file
        </button>
        <button
          type="button"
          role="tab"
          id="tab-record"
          aria-selected={source === "record"}
          aria-controls="panel-record"
          className={source === "record" ? "tab is-active" : "tab"}
          onClick={() => setSource("record")}
        >
          <Microphone size={16} aria-hidden="true" />
          Record audio
        </button>
      </div>

      {source === "file" ? (
        <div
          role="tabpanel"
          id="panel-file"
          aria-labelledby="tab-file"
          className="tab-panel"
        >
          <div
            className={dragging ? "drop-zone is-dragging" : "drop-zone"}
            data-testid="drop-zone"
            onDragOver={(event) => {
              event.preventDefault();
              setDragging(true);
            }}
            onDragLeave={() => setDragging(false)}
            onDrop={onDrop}
            onClick={(event) => {
              if (event.target !== inputRef.current) inputRef.current?.click();
            }}
          >
            <span className="drop-zone-icon" aria-hidden="true">
              <UploadSimple size={22} weight="bold" />
            </span>
            <p className="drop-zone-title">
              Drop an audio file here, or browse
            </p>
            <div className="field">
              <label htmlFor="file">Audio file</label>
              <input
                ref={inputRef}
                id="file"
                type="file"
                accept="audio/*,video/*,.wav,.mp3,.m4a,.flac,.ogg,.webm"
                onChange={(event) => {
                  const chosen = event.target.files?.[0];
                  if (chosen) selectFile(chosen, "upload");
                }}
              />
            </div>
            <p className="hint">
              WAV, MP3, M4A, FLAC, OGG or WebM. The container is validated by
              its actual content, never by the file extension.
            </p>
          </div>

          {file && origin === "upload" && (
            <div className="selection">
              <span className="selection-name">{file.name}</span>
              <span className="badge badge-neutral">{formatBytes(file.size)}</span>
              <button type="button" className="ghost" onClick={clearSelection}>
                <X size={14} aria-hidden="true" />
                Clear
              </button>
            </div>
          )}
        </div>
      ) : (
        <div
          role="tabpanel"
          id="panel-record"
          aria-labelledby="tab-record"
          className="tab-panel"
        >
          <Recorder
            onRecorded={onRecorded}
            onDiscarded={clearSelection}
            language={language}
          />
          {file && origin === "recording" && (
            <div className="selection">
              <span className="selection-name">{file.name}</span>
              <span className="badge badge-neutral">{formatBytes(file.size)}</span>
            </div>
          )}
        </div>
      )}

      <div className="form-row">
        <div className="field">
          {providersError !== null && (
            <div role="alert" className="error">
              could not load providers: {providersError}{" "}
              <button type="button" onClick={() => void loadProviders()}>
                <ArrowsClockwise size={14} aria-hidden="true" />
                Retry
              </button>
            </div>
          )}
          {providers === null && providersError === null && (
            <p className="hint">Loading providers…</p>
          )}
          {providers !== null && providers.length > 1 && (
            <>
              <label htmlFor="provider">Provider</label>
              <select
                id="provider"
                value={provider}
                onChange={(event) => setProvider(event.target.value)}
              >
                <option value="">Default</option>
                {providers.map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.name}
                    {p.is_local && !/\(local\)/i.test(p.name) ? " (local)" : ""}
                  </option>
                ))}
              </select>
            </>
          )}
          {providers !== null && providers.length === 1 && (
            <>
              <label htmlFor="provider">Provider</label>
              <select id="provider" value={providers[0].id} disabled>
                <option value={providers[0].id}>
                  {providers[0].name}
                  {providers[0].is_local && !/\(local\)/i.test(providers[0].name)
                    ? " (local)"
                    : ""}
                </option>
              </select>
            </>
          )}
        </div>

        <div className="field">
          <label htmlFor="language">Language</label>
          <input
            id="language"
            type="text"
            value={language}
            placeholder="auto-detect"
            onChange={(event) => setLanguage(event.target.value)}
          />
          <span className="hint">
            Defaults to English — clear the field to let the provider
            auto-detect.
          </span>
        </div>
      </div>

      {submitError !== null && (
        <p role="alert" className="error">
          <strong>{submitError.code}</strong> {submitError.message}
        </p>
      )}

      <div className="submit-row">
        <button type="submit" className="primary" disabled={!file || submitting}>
          <Waveform size={16} weight="bold" aria-hidden="true" />
          {submitting ? "Submitting…" : "Transcribe"}
        </button>
        <span className="hint">
          {file
            ? "Ready when you are."
            : "Pick a file or record a clip to enable transcription."}
        </span>
      </div>
      </form>
    </section>
  );
}

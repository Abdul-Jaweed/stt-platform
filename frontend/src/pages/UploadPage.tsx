import { useCallback, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";

import { useApi } from "../api/context";
import { ApiError } from "../api/errors";
import type { ProviderInfo } from "../api/types";
import { formatBytes } from "../lib/format";
import { setRetainedAudio } from "../lib/retainedAudio";

interface SubmitError {
  code: string;
  message: string;
}

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
  const [file, setFile] = useState<File | null>(null);
  const [provider, setProvider] = useState("");
  const [language, setLanguage] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<SubmitError | null>(null);

  const loadProviders = useCallback(async () => {
    setProvidersError(null);
    try {
      const list = await api.getProviders();
      setProviders(list);
    } catch (err) {
      setProvidersError(
        err instanceof ApiError ? err.message : String(err),
      );
    }
  }, [api]);

  useEffect(() => {
    void loadProviders();
  }, [loadProviders]);

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
      <h2>New transcription</h2>
      <form onSubmit={onSubmit}>
        <div className="field">
          <label htmlFor="file">Audio file</label>
          <input
            id="file"
            type="file"
            accept="audio/*,video/*,.wav,.mp3,.m4a,.flac,.ogg,.webm"
            onChange={(event) => setFile(event.target.files?.[0] ?? null)}
          />
          {file && (
            <p className="hint">
              {file.name} — {formatBytes(file.size)}
            </p>
          )}
        </div>

        <div className="field">
          {providersError !== null && (
            <div role="alert" className="error">
              could not load providers: {providersError}{" "}
              <button type="button" onClick={() => void loadProviders()}>
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
            <p className="hint">
              Provider: {providers[0].name}
              {providers[0].is_local && !/\(local\)/i.test(providers[0].name)
                ? " (local)"
                : ""}
            </p>
          )}
        </div>

        <div className="field">
          <label htmlFor="language">Language (optional)</label>
          <input
            id="language"
            type="text"
            value={language}
            placeholder="auto-detect"
            onChange={(event) => setLanguage(event.target.value)}
          />
        </div>

        {submitError !== null && (
          <p role="alert" className="error">
            <strong>{submitError.code}</strong> {submitError.message}
          </p>
        )}

        <button type="submit" disabled={!file || submitting}>
          {submitting ? "Submitting…" : "Transcribe"}
        </button>
      </form>
    </section>
  );
}

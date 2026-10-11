import { ArrowsClockwise, Check, Minus } from "@phosphor-icons/react";
import { useCallback, useEffect, useState } from "react";

import { useApi } from "../api/context";
import { ApiError } from "../api/errors";
import type { ProviderInfo } from "../api/types";
import { formatDuration } from "../lib/format";

interface LoadError {
  code: string;
  message: string;
}

/**
 * Capability cells: an icon for the eye, the word for the reader (and for the
 * tests) — colour and shape are never the only signal.
 */
function Capability({ value }: { value: boolean }) {
  return (
    <span className={value ? "cap cap-yes" : "cap cap-no"}>
      {value ? (
        <Check size={14} weight="bold" aria-hidden="true" />
      ) : (
        <Minus size={14} aria-hidden="true" />
      )}
      {value ? "yes" : "no"}
    </span>
  );
}

export default function ProvidersPage() {
  const api = useApi();
  const [providers, setProviders] = useState<ProviderInfo[] | null>(null);
  const [error, setError] = useState<LoadError | null>(null);

  const load = useCallback(async () => {
    setError(null);
    setProviders(null);
    try {
      setProviders(await api.getProviders());
    } catch (err) {
      setError(
        err instanceof ApiError
          ? { code: err.code, message: err.message }
          : { code: "UNKNOWN_ERROR", message: String(err) },
      );
    }
  }, [api]);

  useEffect(() => {
    void load();
  }, [load]);

  if (error) {
    return (
      <section className="page providers-page">
        <h2>Providers</h2>
        <p role="alert" className="error">
          <strong>{error.code}</strong> {error.message}
        </p>
        <button type="button" onClick={() => void load()}>
          <ArrowsClockwise size={14} aria-hidden="true" />
          Retry
        </button>
      </section>
    );
  }

  if (!providers) {
    return <p className="hint">Loading providers…</p>;
  }

  return (
    <section className="page providers-page">
      <h2>Providers</h2>
      <table>
        <thead>
          <tr>
            <th>Provider</th>
            <th>Health</th>
            <th>Languages</th>
            <th>Word timestamps</th>
            <th>Diarization</th>
            <th>Streaming</th>
            <th>Formats</th>
            <th>Max duration</th>
          </tr>
        </thead>
        <tbody>
          {providers.map((provider) => (
            <tr key={provider.id}>
              <td>
                {provider.name}
                {provider.is_local && !/\(local\)/i.test(provider.name)
                  ? " (local)"
                  : ""}
              </td>
              <td>{provider.health.status}</td>
              <td>{provider.capabilities.languages.length}</td>
              <td>
                  <Capability value={provider.capabilities.word_timestamps} />
                </td>
              <td>
                  <Capability value={provider.capabilities.diarization} />
                </td>
              <td>
                  <Capability value={provider.capabilities.streaming} />
                </td>
              <td>{provider.capabilities.formats.join(", ")}</td>
              <td>
                {provider.capabilities.max_duration_s != null
                  ? formatDuration(provider.capabilities.max_duration_s)
                  : "—"}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}

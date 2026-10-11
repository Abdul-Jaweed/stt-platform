import { useCallback, useEffect, useState } from "react";

import { useApi } from "../api/context";
import { ApiError } from "../api/errors";
import type {
  EvaluationComparisonRow,
  EvaluationRecord,
  MlflowOutcome,
  ProviderInfo,
} from "../api/types";

function percent(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  return `${(value * 100).toFixed(1)}%`;
}

function seconds(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  return `${value.toFixed(2)}s`;
}

function ApiFailure({ error }: { error: { code: string; message: string } }) {
  return (
    <p role="alert" className="error">
      <strong>{error.code}</strong> {error.message}
    </p>
  );
}

function MlflowStatus({ outcome }: { outcome: MlflowOutcome | null }) {
  if (!outcome) {
    return (
      <p className="hint" data-testid="mlflow-status">
        MLflow: not recorded for this evaluation.
      </p>
    );
  }
  if (outcome.status === "logged") {
    return (
      <p className="hint" data-testid="mlflow-status">
        MLflow run {outcome.run_id} in experiment {outcome.experiment}{" "}
        {outcome.tracking_uri && (
          <a href={outcome.tracking_uri} target="_blank" rel="noreferrer">
            MLflow
          </a>
        )}
      </p>
    );
  }
  if (outcome.status === "disabled") {
    return (
      <p className="hint" data-testid="mlflow-status">
        MLflow logging is disabled (STT_MLFLOW_ENABLED=false).
      </p>
    );
  }
  return (
    <p className="hint" data-testid="mlflow-status">
      MLflow: {outcome.status} — {outcome.reason}
    </p>
  );
}

function ComparisonTable({
  rows,
  onSelect,
}: {
  rows: EvaluationComparisonRow[];
  onSelect: (id: string) => void;
}) {
  if (rows.length === 0) {
    return <p className="hint">No evaluations yet. Run one above.</p>;
  }
  return (
    <table aria-label="Evaluations">
      <thead>
        <tr>
          <th scope="col">Provider</th>
          <th scope="col">Model</th>
          <th scope="col">Dataset</th>
          <th scope="col">Clips</th>
          <th scope="col">WER</th>
          <th scope="col">CER</th>
          <th scope="col">RTF</th>
          <th scope="col">Word timestamps</th>
          <th scope="col">Evidence</th>
          <th scope="col">Run</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((row) => (
          <tr key={row.evaluation_id}>
            <td>{row.provider}</td>
            <td>{row.model ?? "—"}</td>
            <td>
              {row.dataset_id}@{row.dataset_version}
            </td>
            <td>
              {row.clips_scored}/{row.clips_total}
              {row.failures > 0 && ` (${row.failures} failed)`}
            </td>
            <td>{percent(row.wer_micro)}</td>
            <td>{percent(row.cer_micro)}</td>
            <td>{row.rtf_mean === null ? "—" : row.rtf_mean.toFixed(3)}</td>
            <td>
              {row.word_availability_mean === null
                ? "not reported"
                : percent(row.word_availability_mean)}
            </td>
            <td>
              {row.insufficient_evidence ? (
                <span className="badge">
                  insufficient — {row.clips_scored} clip
                  {row.clips_scored === 1 ? "" : "s"}
                </span>
              ) : (
                "ok"
              )}
            </td>
            <td>
              <button type="button" onClick={() => onSelect(row.evaluation_id)}>
                {row.evaluation_id}
              </button>
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function ClipTable({ record }: { record: EvaluationRecord }) {
  return (
    <table aria-label="Clips">
      <thead>
        <tr>
          <th scope="col">Clip</th>
          <th scope="col">Status</th>
          <th scope="col">WER</th>
          <th scope="col">CER</th>
          <th scope="col">Processing</th>
          <th scope="col">RTF</th>
          <th scope="col">Words</th>
          <th scope="col">Error</th>
        </tr>
      </thead>
      <tbody>
        {record.clips.map((clip) => (
          <tr key={clip.clip_id}>
            <td>{clip.clip_id}</td>
            <td>{clip.status}</td>
            <td>{percent(clip.wer)}</td>
            <td>{percent(clip.cer)}</td>
            <td>{seconds(clip.timings.processing_s)}</td>
            <td>
              {clip.timings.real_time_factor === null ||
              clip.timings.real_time_factor === undefined
                ? "—"
                : clip.timings.real_time_factor.toFixed(3)}
            </td>
            <td>
              {clip.timestamps?.word_availability === null ||
              clip.timestamps?.word_availability === undefined
                ? "not reported"
                : percent(clip.timestamps.word_availability as number)}
            </td>
            <td>{clip.error ?? "—"}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export default function EvaluationsPage() {
  const api = useApi();
  const [rows, setRows] = useState<EvaluationComparisonRow[]>([]);
  const [providers, setProviders] = useState<ProviderInfo[]>([]);
  const [datasetId, setDatasetId] = useState("stt-eval-core");
  const [language, setLanguage] = useState("en");
  const [provider, setProvider] = useState("");
  const [running, setRunning] = useState(false);
  const [runError, setRunError] = useState<{
    code: string;
    message: string;
  } | null>(null);
  const [detail, setDetail] = useState<EvaluationRecord | null>(null);
  const [loadId, setLoadId] = useState("");
  const [loadError, setLoadError] = useState<{
    code: string;
    message: string;
  } | null>(null);

  const refresh = useCallback(async () => {
    try {
      const data = await api.listEvaluations();
      setRows(data.evaluations);
    } catch {
      // An empty comparison table is better than a broken page; the run
      // form surfaces its own errors.
      setRows([]);
    }
  }, [api]);

  useEffect(() => {
    void refresh();
    void api
      .getProviders()
      .then(setProviders)
      .catch(() => setProviders([]));
  }, [api, refresh]);

  async function run() {
    setRunning(true);
    setRunError(null);
    try {
      const record = await api.createEvaluation({
        dataset_id: datasetId.trim(),
        language: language.trim() || undefined,
        provider: provider || undefined,
      });
      setDetail(record);
      await refresh();
    } catch (err) {
      setRunError(toFailure(err));
    } finally {
      setRunning(false);
    }
  }

  async function load(id: string) {
    setLoadError(null);
    try {
      setDetail(await api.getEvaluation(id));
    } catch (err) {
      setLoadError(toFailure(err));
    }
  }

  return (
    <section className="page evaluations-page">
      <h2>Evaluations</h2>

      <div className="field">
        <label htmlFor="dataset-id">Dataset</label>
        <input
          id="dataset-id"
          value={datasetId}
          onChange={(event) => setDatasetId(event.target.value)}
        />
        <label htmlFor="eval-language">Language</label>
        <input
          id="eval-language"
          value={language}
          onChange={(event) => setLanguage(event.target.value)}
        />
        <label htmlFor="eval-provider">Provider</label>
        <select
          id="eval-provider"
          value={provider}
          onChange={(event) => setProvider(event.target.value)}
        >
          <option value="">default</option>
          {providers.map((item) => (
            <option key={item.id} value={item.id}>
              {item.id}
            </option>
          ))}
        </select>
        <button type="button" onClick={() => void run()} disabled={running}>
          Run evaluation
        </button>
      </div>
      <p className="hint">
        Runs synchronously against the reference corpus; the provider that
        actually ran is recorded per clip.
      </p>
      {runError && <ApiFailure error={runError} />}

      <h3>Comparison</h3>
      <ComparisonTable rows={rows} onSelect={(id) => void load(id)} />

      <h3>Detail</h3>
      <div className="field">
        <label htmlFor="evaluation-id">Evaluation ID</label>
        <input
          id="evaluation-id"
          value={loadId}
          onChange={(event) => setLoadId(event.target.value)}
        />
        <button type="button" onClick={() => void load(loadId.trim())}>
          Load
        </button>
      </div>
      {loadError && <ApiFailure error={loadError} />}

      {detail && (
        <div className="evaluation-detail">
          <h4>
            {detail.evaluation_id} — {detail.provider}
            {detail.model ? ` (${detail.model})` : ""}
          </h4>
          <p className="hint">
            {detail.dataset_id}@{detail.dataset_version} ·{" "}
            {String(detail.aggregate.clips_scored ?? 0)}/
            {String(detail.aggregate.clips_total ?? 0)} clips scored · WER{" "}
            {percent(detail.aggregate.wer_micro as number | null)} · CER{" "}
            {percent(detail.aggregate.cer_micro as number | null)} · RTF{" "}
            {detail.aggregate.rtf_mean === null ||
            detail.aggregate.rtf_mean === undefined
              ? "—"
              : (detail.aggregate.rtf_mean as number).toFixed(3)}{" "}
            · normalization {String(detail.normalization.name ?? "unknown")}
          </p>
          {detail.insufficient_evidence && (
            <p className="hint">
              Insufficient evidence to rank: too few scored clips.
            </p>
          )}
          <MlflowStatus outcome={detail.mlflow} />
          <ClipTable record={detail} />
        </div>
      )}
    </section>
  );
}

function toFailure(err: unknown): { code: string; message: string } {
  if (err instanceof ApiError) return { code: err.code, message: err.message };
  return { code: "UNKNOWN_ERROR", message: String(err) };
}

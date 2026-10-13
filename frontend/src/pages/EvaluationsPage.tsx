import {
  ArrowsClockwise,
  ArrowsDownUp,
  ChartBar,
  MagnifyingGlass,
  Play,
  WarningCircle,
} from "@phosphor-icons/react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { useApi } from "../api/context";
import { ApiError } from "../api/errors";
import type {
  EvaluationDatasetInfo,
  EvaluationArtifact,
  EvaluationComparisonRow,
  EvaluationRecord,
  MetricExplanation,
  ProviderInfo,
} from "../api/types";
import EvaluationRunDetail from "../components/EvaluationRunDetail";
import WerTrend from "../components/WerTrend";

/** How many runs the overview shows before it offers the rest. */
const PAGE_SIZE = 8;

type SortKey = "started" | "wer" | "cer" | "rtf";
type SortDir = "asc" | "desc";

/**
 * A meter for a rate that is 0..1 by definition. `null` stays a statement
 * ("not reported"), never a zero-length bar — the platform never turns a
 * missing measurement into a good-looking one.
 */
function Meter({
  value,
  label,
  max = 1,
}: {
  value: number | null;
  label: string;
  max?: number;
}) {
  if (value === null || value === undefined) {
    return <span className="hint">not reported</span>;
  }
  const percentOfMax = Math.max(0, Math.min(100, Math.round((value / max) * 100)));
  return (
    <span
      className="meter"
      role="meter"
      aria-label={label}
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={percentOfMax}
      aria-valuetext={`${percentOfMax}%`}
    >
      <span
        className="meter-fill"
        style={{ width: `${Math.max(2, percentOfMax)}%` }}
      />
    </span>
  );
}

function percent(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  return `${(value * 100).toFixed(1)}%`;
}

function shortDate(value: string | null | undefined): string {
  if (!value) return "—";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return value;
  return parsed.toLocaleString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function ApiFailure({
  error,
  onRetry,
}: {
  error: { code: string; message: string };
  onRetry?: () => void;
}) {
  return (
    <p role="alert" className="error">
      <WarningCircle size={16} weight="bold" aria-hidden="true" />
      <strong>{error.code}</strong> {error.message}
      {onRetry && (
        <button type="button" className="ghost" onClick={onRetry}>
          <ArrowsClockwise size={14} aria-hidden="true" />
          Retry
        </button>
      )}
    </p>
  );
}

/** Summary numbers, every one of them derived from the runs themselves. */
function Summary({ rows }: { rows: EvaluationComparisonRow[] }) {
  const measured = rows
    .map((row) => row.wer_micro)
    .filter((value): value is number => value !== null && value !== undefined);
  const bestWer = measured.length > 0 ? Math.min(...measured) : null;
  const providers = new Set(rows.map((row) => row.provider)).size;
  const withFailures = rows.filter((row) => row.failures > 0).length;

  return (
    <dl className="stat-grid" role="group" aria-label="Evaluation summary">
      <div className="stat">
        <dt>Runs</dt>
        <dd>{rows.length}</dd>
      </div>
      <div className="stat">
        <dt>Best WER</dt>
        <dd>{bestWer === null ? "not reported" : percent(bestWer)}</dd>
      </div>
      <div className="stat">
        <dt>Providers</dt>
        <dd>{providers}</dd>
      </div>
      <div className="stat">
        <dt>With failures</dt>
        <dd>{withFailures}</dd>
      </div>
    </dl>
  );
}

function SortHeader({
  label,
  sortKey,
  sort,
  onSort,
}: {
  label: string;
  sortKey: SortKey;
  sort: { key: SortKey; dir: SortDir };
  onSort: (key: SortKey) => void;
}) {
  const active = sort.key === sortKey;
  return (
    <th
      scope="col"
      aria-sort={
        active ? (sort.dir === "asc" ? "ascending" : "descending") : "none"
      }
    >
      <button
        type="button"
        className={active ? "sort-button is-active" : "sort-button"}
        onClick={() => onSort(sortKey)}
      >
        {label}
        <ArrowsDownUp size={12} weight="bold" aria-hidden="true" />
      </button>
    </th>
  );
}

function ComparisonTable({
  rows,
  sort,
  onSort,
  onSelect,
}: {
  rows: EvaluationComparisonRow[];
  sort: { key: SortKey; dir: SortDir };
  onSort: (key: SortKey) => void;
  onSelect: (id: string) => void;
}) {
  return (
    <div className="table-scroll">
      <table aria-label="Evaluations">
        <thead>
          <tr>
            <th scope="col">Provider</th>
            <th scope="col">Model</th>
            <th scope="col">Dataset</th>
            <th scope="col">Clips</th>
            <SortHeader label="WER" sortKey="wer" sort={sort} onSort={onSort} />
            <SortHeader label="CER" sortKey="cer" sort={sort} onSort={onSort} />
            <SortHeader label="RTF" sortKey="rtf" sort={sort} onSort={onSort} />
            <th scope="col">Word timestamps</th>
            <th scope="col">Evidence</th>
            <SortHeader
              label="Started"
              sortKey="started"
              sort={sort}
              onSort={onSort}
            />
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
              <td>
                <Meter value={row.wer_micro} label="Word error rate" />
                {percent(row.wer_micro)}
              </td>
              <td>
                <Meter value={row.cer_micro} label="Character error rate" />
                {percent(row.cer_micro)}
              </td>
              <td>
                <Meter value={row.rtf_mean} label="Real-time factor" max={0.5} />
                {row.rtf_mean === null ? "—" : row.rtf_mean.toFixed(3)}
              </td>
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
              <td>{shortDate(row.started_at)}</td>
              <td>
                <button
                  type="button"
                  className="link-button"
                  onClick={() => onSelect(row.evaluation_id)}
                >
                  {row.evaluation_id}
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export default function EvaluationsPage() {
  const api = useApi();
  const [rows, setRows] = useState<EvaluationComparisonRow[]>([]);
  const [listError, setListError] = useState<{
    code: string;
    message: string;
  } | null>(null);
  const [providers, setProviders] = useState<ProviderInfo[]>([]);
  const [datasets, setDatasets] = useState<EvaluationDatasetInfo[]>([]);
  const [datasetsListFailed, setDatasetsListFailed] = useState(false);
  const [datasetId, setDatasetId] = useState("stt-eval-core");
  const [language, setLanguage] = useState("en");
  const [provider, setProvider] = useState("");
  const [running, setRunning] = useState(false);
  const [elapsed, setElapsed] = useState(0);
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
  const [artifact, setArtifact] = useState<EvaluationArtifact | null>(null);
  const [artifactError, setArtifactError] = useState<string | null>(null);
  const [metrics, setMetrics] = useState<MetricExplanation[]>([]);
  const [query, setQuery] = useState("");
  const [onlyFailures, setOnlyFailures] = useState(false);
  const [sort, setSort] = useState<{ key: SortKey; dir: SortDir }>({
    key: "started",
    dir: "desc",
  });
  const [visible, setVisible] = useState(PAGE_SIZE);
  const inFlight = useRef(false);
  const datasetInput = useRef<HTMLInputElement | null>(null);
  const datasetSelect = useRef<HTMLSelectElement | null>(null);

  const refresh = useCallback(async () => {
    try {
      const data = await api.listEvaluations();
      setRows(data.evaluations);
      setListError(null);
    } catch (error) {
      // An empty table would quietly claim there is no history: say what
      // happened and offer to try again.
      setRows([]);
      setListError(toFailure(error));
    }
  }, [api]);

  useEffect(() => {
    void refresh();
    void api
      .getProviders()
      .then(setProviders)
      .catch(() => setProviders([]));
    // The dataset picker is a convenience, not a gate: a deployment that
    // cannot list its datasets still lets you type an id below.
    void api
      .listEvaluationDatasets()
      .then((list) => {
        setDatasets(list.datasets);
        setDatasetId((current) =>
          list.datasets.some((item) => item.dataset_id === current)
            ? current
            : (list.datasets[0]?.dataset_id ?? current),
        );
      })
      .catch(() => setDatasetsListFailed(true));
  }, [api, refresh]);

  // A run is synchronous and can take a while: show that it is alive rather
  // than leaving a frozen button behind.
  useEffect(() => {
    if (!running) {
      setElapsed(0);
      return;
    }
    const timer = window.setInterval(() => setElapsed((value) => value + 1), 1000);
    return () => window.clearInterval(timer);
  }, [running]);

  const filtered = useMemo(() => {
    const needle = query.trim().toLowerCase();
    const matching = rows.filter((row) => {
      if (onlyFailures && row.failures === 0) return false;
      if (!needle) return true;
      return [row.provider, row.model ?? "", row.dataset_id, row.evaluation_id]
        .join(" ")
        .toLowerCase()
        .includes(needle);
    });
    const direction = sort.dir === "asc" ? 1 : -1;
    const value = (row: EvaluationComparisonRow): number | null => {
      if (sort.key === "wer") return row.wer_micro;
      if (sort.key === "cer") return row.cer_micro;
      if (sort.key === "rtf") return row.rtf_mean;
      return row.started_at ? Date.parse(row.started_at) : null;
    };
    return [...matching].sort((left, right) => {
      const a = value(left);
      const b = value(right);
      // A metric nobody measured never wins a sort: it goes last either way.
      if (a === null && b === null) return 0;
      if (a === null) return 1;
      if (b === null) return -1;
      return (a - b) * direction;
    });
  }, [rows, query, onlyFailures, sort]);

  const shown = filtered.slice(0, visible);

  function toggleSort(key: SortKey) {
    setSort((current) =>
      current.key === key
        ? { key, dir: current.dir === "asc" ? "desc" : "asc" }
        : { key, dir: key === "started" ? "desc" : "asc" },
    );
  }

  async function run() {
    // A second click while the first request is open would run the corpus
    // twice; the button is disabled, and this is the belt to its braces.
    if (inFlight.current) return;
    inFlight.current = true;
    setRunning(true);
    setRunError(null);
    try {
      const record = await api.createEvaluation({
        dataset_id: datasetId.trim(),
        language: language.trim() || undefined,
        provider: provider || undefined,
      });
      // Show it the moment it exists — record, artifact and explanations —
      // the same way a run opened from the table is shown.
      await load(record.evaluation_id);
      await refresh();
    } catch (err) {
      setRunError(toFailure(err));
    } finally {
      inFlight.current = false;
      setRunning(false);
    }
  }

  async function load(id: string) {
    setLoadError(null);
    if (!id) return;
    // Three independent reads. The record is required; the artifact and the
    // metric explanations are not — a deployment without storage must still
    // render the run, with the honest note, rather than nothing at all.
    const [recordResult, artifactResult, metricsResult] =
      await Promise.allSettled([
        api.getEvaluation(id),
        api.getEvaluationArtifact(id),
        api.getEvaluationMetrics(),
      ]);
    if (recordResult.status === "rejected") {
      setLoadError(toFailure(recordResult.reason));
      return;
    }
    setDetail(recordResult.value);
    setArtifact(
      artifactResult.status === "fulfilled" ? artifactResult.value : null,
    );
    setArtifactError(
      artifactResult.status === "fulfilled"
        ? null
        : describeFailure(artifactResult.reason),
    );
    setMetrics(
      metricsResult.status === "fulfilled"
        ? (metricsResult.value.metrics ?? [])
        : [],
    );
  }

  const selectedDataset =
    datasets.find((item) => item.dataset_id === datasetId) ?? null;

  return (
    <section className="page evaluations-page">
      <div className="page-head">
        <div>
          <h2>Evaluations</h2>
          <p className="hint">
            Compare providers on the same reference corpus. Every number was
            measured; anything that could not be measured says so instead.
          </p>
        </div>
        <button type="button" className="ghost" onClick={() => void refresh()}>
          <ArrowsClockwise size={14} aria-hidden="true" />
          Refresh
        </button>
      </div>

      <Summary rows={rows} />

      {rows.length > 0 && (
        <section className="card" aria-labelledby="trend-heading">
          <h3 id="trend-heading">WER trend</h3>
          <WerTrend runs={rows} />
        </section>
      )}

      <section className="card" aria-labelledby="run-heading">
        <h3 id="run-heading">Run an evaluation</h3>
        <div className="run-form">
          <div className="field">
            <label htmlFor="dataset-id">Dataset</label>
            {datasets.length > 0 ? (
              <select
                id="dataset-id"
                ref={datasetSelect}
                value={datasetId}
                onChange={(event) => setDatasetId(event.target.value)}
              >
                {datasets.map((item) => (
                  <option
                    key={item.dataset_id}
                    value={item.dataset_id}
                    disabled={Boolean(item.load_error)}
                  >
                    {item.dataset_id} (v{item.version} · {item.clips_scored}{" "}
                    scored)
                  </option>
                ))}
              </select>
            ) : (
              <input
                id="dataset-id"
                ref={datasetInput}
                value={datasetId}
                onChange={(event) => setDatasetId(event.target.value)}
              />
            )}
            {selectedDataset && (
              <p className="hint">
                {selectedDataset.load_error
                  ? `unreadable manifest — ${selectedDataset.load_error}`
                  : `v${selectedDataset.version} · ${selectedDataset.clips_scored} scored of ${selectedDataset.clips_total} clips` +
                    (selectedDataset.clips_unscored > 0
                      ? ` · ${selectedDataset.clips_unscored} awaiting a reference`
                      : "")}
              </p>
            )}
            {selectedDataset &&
              !selectedDataset.audio_ready &&
              !selectedDataset.load_error && (
                <p className="hint dataset-warning" role="status">
                  <WarningCircle size={14} weight="fill" aria-hidden="true" />{" "}
                  audio missing for {selectedDataset.clips_missing_audio}{" "}
                  clip(s) — run scripts/make_eval_corpus.py
                </p>
              )}
            {datasets.length === 0 && (
              <p className="hint">
                {datasetsListFailed
                  ? "the dataset list could not be loaded — enter an id"
                  : "no datasets are listed by this deployment — enter an id"}
              </p>
            )}
          </div>
          <div className="field">
            <label htmlFor="eval-language">Language</label>
            <input
              id="eval-language"
              value={language}
              onChange={(event) => setLanguage(event.target.value)}
            />
          </div>
          <div className="field">
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
          </div>
          <button
            type="button"
            className="primary"
            onClick={() => void run()}
            disabled={running}
          >
            <Play size={15} weight="fill" aria-hidden="true" />
            {running ? "Running…" : "Run evaluation"}
          </button>
        </div>
        {running && (
          <div
            className="run-progress"
            role="progressbar"
            aria-label="Evaluation in progress"
            aria-valuetext={`running for ${elapsed}s`}
          >
            <span className="run-progress-bar" aria-hidden="true" />
            <span className="hint">
              running for {elapsed}s — evaluations run synchronously on the
              server, so this page waits for the result
            </span>
          </div>
        )}
        {runError && <ApiFailure error={runError} />}
        <p className="hint">
          The provider that actually ran is recorded per clip, not assumed.
        </p>
      </section>

      <section className="card" aria-labelledby="compare-heading">
        <div className="table-head">
          <h3 id="compare-heading">Comparison</h3>
          <div className="table-tools">
            <label className="search-field">
              <MagnifyingGlass size={15} aria-hidden="true" />
              <input
                type="search"
                aria-label="Search runs"
                placeholder="Provider, model or dataset"
                value={query}
                onChange={(event) => setQuery(event.target.value)}
              />
            </label>
            <label className="check-field">
              <input
                type="checkbox"
                checked={onlyFailures}
                onChange={(event) => setOnlyFailures(event.target.checked)}
              />
              Only runs with failures
            </label>
          </div>
        </div>

        {listError ? (
          <ApiFailure error={listError} onRetry={() => void refresh()} />
        ) : rows.length === 0 ? (
          <div className="empty-state" data-testid="evaluations-empty">
            <ChartBar size={26} weight="bold" aria-hidden="true" />
            <h4>No evaluations yet</h4>
            <p className="hint">
              An evaluation scores a provider against the reference corpus.
              Generate the corpus first on a fresh checkout, then run one
              above.
            </p>
            <button
              type="button"
              className="ghost"
              onClick={() => (datasetSelect.current ?? datasetInput.current)?.focus()}
            >
              Run an evaluation
            </button>
          </div>
        ) : (
          <>
            <ComparisonTable
              rows={shown}
              sort={sort}
              onSort={toggleSort}
              onSelect={(id) => void load(id)}
            />
            {filtered.length > shown.length && (
              <button
                type="button"
                className="ghost show-more"
                onClick={() => setVisible((value) => value + PAGE_SIZE)}
              >
                Show {filtered.length - shown.length} more
              </button>
            )}
            {filtered.length === 0 && (
              <p className="hint">
                No runs match this filter — clear the search to see all{" "}
                {rows.length}.
              </p>
            )}
          </>
        )}
      </section>

      <section className="card" aria-labelledby="open-heading">
        <h3 id="open-heading">Open a run by ID</h3>
        <div className="run-form">
          <div className="field">
            <label htmlFor="evaluation-id">Evaluation ID</label>
            <input
              id="evaluation-id"
              value={loadId}
              onChange={(event) => setLoadId(event.target.value)}
            />
          </div>
          <button type="button" onClick={() => void load(loadId.trim())}>
            Load
          </button>
        </div>
        {loadError && <ApiFailure error={loadError} />}
      </section>

      {detail && (
        <EvaluationRunDetail
          key={detail.evaluation_id}
          record={detail}
          artifact={artifact}
          artifactError={artifactError}
          metrics={metrics}
          onClose={() => {
            setDetail(null);
            setArtifact(null);
            setArtifactError(null);
            setMetrics([]);
          }}
        />
      )}
    </section>
  );
}

function toFailure(err: unknown): { code: string; message: string } {
  if (err instanceof ApiError) return { code: err.code, message: err.message };
  return { code: "UNKNOWN_ERROR", message: String(err) };
}

/**
 * The artifact endpoint answers honestly when it has nothing to serve: 404
 * ARTIFACT_NOT_FOUND for a run that stored none, 503 STORAGE_NOT_CONFIGURED
 * for a deployment without storage. Keep the code in front so the two cases
 * stay distinguishable where the detail renders the note.
 */
function describeFailure(err: unknown): string {
  const failure = toFailure(err);
  return `${failure.code}: ${failure.message}`;
}

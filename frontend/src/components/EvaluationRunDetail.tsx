/**
 * One evaluation run, in full: the headline numbers with the server's own
 * explanations, the clips the run attempted, the words behind a chosen clip,
 * and the methodology that makes the numbers readable.
 *
 * Two rules shape every section. A measurement nobody took says "not
 * measured" — never a zero that reads like a good number. And a number is
 * never explained by this component: the formula, the callable and the
 * caveats come from `GET /v1/evaluations/metrics`, or the card says there is
 * no explanation. The diff is read off the same alignment the run scored
 * with, so the picture and the numbers cannot disagree.
 */
import {
  ArrowsDownUp,
  CaretLeft,
  CaretRight,
  Copy,
  MagnifyingGlass,
  WarningCircle,
  X,
} from "@phosphor-icons/react";
import { useMemo, useState } from "react";

import type {
  EvaluationArtifact,
  EvaluationArtifactClip,
  EvaluationClipSummary,
  EvaluationRecord,
  MetricExplanation,
  MlflowOutcome,
} from "../api/types";
import { alignmentCounts, diffFromAlignment, type DiffPart } from "../lib/alignment";

/** Ten clips to a page: enough to read, few enough to scan. */
const PAGE_SIZE = 10;

/** The run-level numbers the cards headline, in reading order. */
const HEADLINE_METRICS = [
  "wer_micro",
  "wer_macro",
  "cer_micro",
  "cer_macro",
  "failure_rate",
  "rtf_mean",
  "coverage_mean",
] as const;

type SortKey = "clip" | "wer" | "cer";
type SortDir = "asc" | "desc";

function percent(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  return `${(value * 100).toFixed(1)}%`;
}

function rtf(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  return value.toFixed(3);
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

/** A rate is a percentage; rtf is a multiplier; nothing measured is an em
 * dash, spoken aloud by the words beside it. */
function formatMetricValue(key: string, value: number): string {
  if (key === "rtf_mean") return `×${value.toFixed(3)}`;
  return `${(value * 100).toFixed(1)}%`;
}

function statusTone(status: string): string {
  const normalized = status.toLowerCase();
  if (normalized === "completed" || normalized === "succeeded") {
    return "badge badge-ok";
  }
  if (normalized === "failed") return "badge badge-error";
  if (normalized === "cancelled") return "badge badge-muted";
  return "badge badge-neutral";
}

/** `s/d/i` as the run counted them per clip — absent counts stay a dash,
 * never a zero. */
function editCounts(clip: EvaluationClipSummary): string {
  const { substitutions, deletions, insertions } = clip.word_errors;
  if (
    substitutions === undefined &&
    deletions === undefined &&
    insertions === undefined
  ) {
    return "—";
  }
  return `${substitutions ?? 0}/${deletions ?? 0}/${insertions ?? 0}`;
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

function MetricCard({
  metricKey,
  value,
  explanation,
}: {
  metricKey: string;
  value: number | null;
  explanation: MetricExplanation | null;
}) {
  const measured = value !== null && value !== undefined;
  return (
    <article className="metric-card" data-testid={`metric-card-${metricKey}`}>
      <h5 className="metric-label">{explanation ? explanation.name : metricKey}</h5>
      <p className="metric-value" data-testid={`metric-value-${metricKey}`}>
        {measured ? (
          formatMetricValue(metricKey, value)
        ) : (
          <>
            — <span className="metric-unmeasured">not measured</span>
          </>
        )}
      </p>
      {explanation ? (
        <details className="metric-details">
          <summary>What this number means</summary>
          <dl className="metric-facts">
            <div>
              <dt>Formula</dt>
              <dd>{explanation.formula}</dd>
            </div>
            <div>
              <dt>Computed by</dt>
              <dd className="mono-inline">{explanation.computed_by}</dd>
            </div>
            <div>
              <dt>Applies to</dt>
              <dd>{explanation.applies_to}</dd>
            </div>
            <div>
              <dt>Unit</dt>
              <dd>{explanation.unit}</dd>
            </div>
            <div>
              <dt>Direction</dt>
              <dd>{explanation.direction}</dd>
            </div>
            <div>
              <dt>Edge behaviour</dt>
              <dd>{explanation.edge_behaviour}</dd>
            </div>
            <div>
              <dt>Limitations</dt>
              <dd>{explanation.limitations}</dd>
            </div>
          </dl>
        </details>
      ) : (
        <p
          className="hint metric-unexplained"
          data-testid={`metric-unexplained-${metricKey}`}
        >
          no explanation was served for this metric
        </p>
      )}
    </article>
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
  sort: { key: SortKey; dir: SortDir } | null;
  onSort: (key: SortKey) => void;
}) {
  const active = sort?.key === sortKey;
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

function ClipDiff({ clip }: { clip: EvaluationArtifactClip }) {
  if (!clip.alignment) {
    return (
      <section className="clip-diff" aria-label="Reference versus transcript">
        <h4>Reference versus transcript</h4>
        <p className="hint" data-testid="no-transcript-note">
          {clip.clip_id}: this clip produced no transcript — there is nothing to
          compare.
        </p>
        {clip.error && <p className="hint">The attempt reported: {clip.error}</p>}
      </section>
    );
  }

  let parts: DiffPart[];
  try {
    parts = diffFromAlignment(clip.alignment);
  } catch (error) {
    // A corrupt artifact must not take the page down, and must not be
    // papered over: say which clip, and why.
    return (
      <section className="clip-diff" aria-label="Reference versus transcript">
        <h4>Reference versus transcript</h4>
        <p role="alert" className="error" data-testid="alignment-error">
          <WarningCircle size={16} weight="bold" aria-hidden="true" />
          The stored alignment for {clip.clip_id} could not be read:{" "}
          {error instanceof Error ? error.message : String(error)}
        </p>
      </section>
    );
  }

  const counts = alignmentCounts(parts);

  return (
    <section className="clip-diff" aria-label="Reference versus transcript">
      <h4>Reference versus transcript — {clip.clip_id}</h4>
      <ul className="diff-legend" aria-label="Diff legend">
        <li>
          <span className="diff-token diff-equal" aria-hidden="true" />
          equal
        </li>
        <li>
          <span className="diff-token diff-substitute" aria-hidden="true" />
          substitution
        </li>
        <li>
          <span className="diff-token diff-delete" aria-hidden="true" />
          deletion
        </li>
        <li>
          <span className="diff-token diff-insert" aria-hidden="true" />
          insertion
        </li>
      </ul>
      <div className="diff-grid">
        <div className="diff-pane">
          <h5>Reference (human-verified)</h5>
          <p className="diff-tokens" data-testid="diff-reference">
            {parts.map((part, index) =>
              part.reference === undefined ? null : (
                <span
                  key={`reference-${index}`}
                  className={`diff-token diff-${part.kind}`}
                >
                  {part.reference}
                </span>
              ),
            )}
          </p>
        </div>
        <div className="diff-pane">
          <h5>Transcript (what the provider heard)</h5>
          <p className="diff-tokens" data-testid="diff-hypothesis">
            {parts.map((part, index) =>
              part.hypothesis === undefined ? null : (
                <span
                  key={`hypothesis-${index}`}
                  className={`diff-token diff-${part.kind}`}
                >
                  {part.hypothesis}
                </span>
              ),
            )}
          </p>
        </div>
      </div>
      <p className="diff-counts" data-testid="diff-counts">
        substitutions {counts.substitutions} · deletions {counts.deletions} ·
        insertions {counts.insertions} — read off the same alignment the run
        scored with
      </p>
    </section>
  );
}

function GroupTable({ record }: { record: EvaluationRecord }) {
  const groups = Object.entries(record.by_group ?? {});
  if (groups.length === 0) return null;

  return (
    <>
      <h4>By group</h4>
      <div className="table-scroll">
        <table aria-label="By group">
          <thead>
            <tr>
              <th scope="col">Group</th>
              <th scope="col">Provider</th>
              <th scope="col">Clips</th>
              <th scope="col">WER</th>
              <th scope="col">CER</th>
            </tr>
          </thead>
          <tbody>
            {groups.flatMap(([group, entries]) =>
              Object.entries(entries ?? {}).map(([key, metrics]) => (
                <tr key={`${group}:${key}`}>
                  <td>{group}</td>
                  <td>{key}</td>
                  <td>{metrics.clips_scored ?? "—"}</td>
                  <td>{percent(metrics.wer_micro ?? null)}</td>
                  <td>{percent(metrics.cer_micro ?? null)}</td>
                </tr>
              )),
            )}
          </tbody>
        </table>
      </div>
    </>
  );
}

function SettingRows({
  label,
  settings,
}: {
  label: string;
  settings: Record<string, unknown>;
}) {
  const entries = Object.entries(settings);
  if (entries.length === 0) {
    return (
      <div>
        <dt>{label}</dt>
        <dd className="hint">not recorded</dd>
      </div>
    );
  }
  return (
    <>
      {entries.map(([key, value]) => (
        <div key={`${label}-${key}`}>
          <dt>
            {label}: {key}
          </dt>
          <dd>
            {typeof value === "string" ||
            typeof value === "number" ||
            typeof value === "boolean"
              ? String(value)
              : JSON.stringify(value)}
          </dd>
        </div>
      ))}
    </>
  );
}

export interface EvaluationRunDetailProps {
  record: EvaluationRecord;
  artifact: EvaluationArtifact | null;
  artifactError: string | null;
  metrics: MetricExplanation[];
  onClose?: () => void;
}

export default function EvaluationRunDetail({
  record,
  artifact,
  artifactError,
  metrics,
  onClose,
}: EvaluationRunDetailProps) {
  const [query, setQuery] = useState("");
  const [sort, setSort] = useState<{ key: SortKey; dir: SortDir } | null>(null);
  const [page, setPage] = useState(1);
  const [selectedClipId, setSelectedClipId] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);

  const aggregate = artifact?.aggregate ?? record.aggregate;
  const explanations = useMemo(
    () => new Map(metrics.map((metric) => [metric.name, metric])),
    [metrics],
  );

  const clips = record.clips;

  const filtered = useMemo(() => {
    const needle = query.trim().toLowerCase();
    const matching = needle
      ? clips.filter((clip) => clip.clip_id.toLowerCase().includes(needle))
      : clips;
    if (!sort) return matching;
    const direction = sort.dir === "asc" ? 1 : -1;
    return [...matching].sort((left, right) => {
      if (sort.key === "clip") {
        return left.clip_id.localeCompare(right.clip_id) * direction;
      }
      const a = sort.key === "wer" ? left.wer : left.cer;
      const b = sort.key === "wer" ? right.wer : right.cer;
      // A metric nobody measured never wins a sort: it goes last either way.
      if (a === null && b === null) return 0;
      if (a === null) return 1;
      if (b === null) return -1;
      return (a - b) * direction;
    });
  }, [clips, query, sort]);

  const totalPages = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE));
  const currentPage = Math.min(page, totalPages);
  const pageRows = filtered.slice(
    (currentPage - 1) * PAGE_SIZE,
    currentPage * PAGE_SIZE,
  );

  const selectedArtifactClip =
    artifact && selectedClipId
      ? (artifact.clips.find((clip) => clip.clip_id === selectedClipId) ?? null)
      : null;

  function toggleSort(key: SortKey) {
    setSort((current) =>
      current && current.key === key
        ? { key, dir: current.dir === "asc" ? "desc" : "asc" }
        : { key, dir: "asc" },
    );
  }

  async function copyId() {
    const clipboard = navigator.clipboard;
    if (!clipboard || typeof clipboard.writeText !== "function") return;
    try {
      await clipboard.writeText(record.evaluation_id);
      setCopied(true);
    } catch {
      // The browser refused; the id is still selectable text on screen.
      setCopied(false);
    }
  }

  const headlineKeys = HEADLINE_METRICS.filter((key) =>
    Object.prototype.hasOwnProperty.call(aggregate, key),
  );

  return (
    <section className="card evaluation-detail" data-testid="evaluation-detail">
      <div className="detail-head">
        <div>
          <h3 className="run-heading">
            <code className="run-id">{record.evaluation_id}</code>
            <button
              type="button"
              className="ghost copy-button"
              aria-label="Copy evaluation id"
              onClick={() => void copyId()}
            >
              <Copy size={14} aria-hidden="true" />
              {copied ? "Copied" : "Copy"}
            </button>
          </h3>
          <p className="hint detail-subhead">
            {record.provider}
            {record.model ? ` · ${record.model}` : ""} · {record.dataset_id}@
            {record.dataset_version} ·{" "}
            <span className={statusTone(record.status)}>{record.status}</span> ·
            started {shortDate(record.started_at)} · finished{" "}
            {shortDate(record.finished_at)} · ran for{" "}
            {record.duration_s.toFixed(1)}s
          </p>
          <MlflowStatus outcome={record.mlflow} />
        </div>
        {onClose && (
          <button type="button" className="ghost" onClick={onClose}>
            <X size={14} aria-hidden="true" />
            Close
          </button>
        )}
      </div>

      {record.insufficient_evidence && (
        <p className="banner banner-warn" data-testid="insufficient-note">
          <WarningCircle size={16} weight="bold" aria-hidden="true" />
          Insufficient evidence to rank: too few scored clips.
        </p>
      )}

      <h4>Headline numbers</h4>
      {headlineKeys.length === 0 ? (
        <p className="hint">This run reported no headline numbers.</p>
      ) : (
        <div className="metric-cards">
          {headlineKeys.map((key) => (
            <MetricCard
              key={key}
              metricKey={key}
              value={aggregate[key] ?? null}
              explanation={explanations.get(key) ?? null}
            />
          ))}
        </div>
      )}

      <GroupTable record={record} />

      <h4>What it sounded like</h4>
      <p className="hint">
        One row per clip the run attempted. Select a clip to see the
        human-verified reference beside what the provider actually heard.
      </p>
      <div className="table-head">
        <label className="search-field">
          <MagnifyingGlass size={15} aria-hidden="true" />
          <input
            type="search"
            aria-label="Search clips"
            placeholder="Clip id"
            value={query}
            onChange={(event) => {
              setQuery(event.target.value);
              setPage(1);
            }}
          />
        </label>
      </div>
      <div className="table-scroll">
        <table aria-label="Clips">
          <thead>
            <tr>
              <SortHeader label="Clip" sortKey="clip" sort={sort} onSort={toggleSort} />
              <th scope="col">Status</th>
              <SortHeader label="WER" sortKey="wer" sort={sort} onSort={toggleSort} />
              <SortHeader label="CER" sortKey="cer" sort={sort} onSort={toggleSort} />
              <th scope="col">S / D / I</th>
              <th scope="col">Provider</th>
              <th scope="col">RTF</th>
            </tr>
          </thead>
          <tbody>
            {pageRows.map((clip) => (
              <tr
                key={clip.clip_id}
                className={clip.clip_id === selectedClipId ? "is-selected" : undefined}
              >
                <td>
                  <button
                    type="button"
                    className="link-button"
                    onClick={() => setSelectedClipId(clip.clip_id)}
                  >
                    {clip.clip_id}
                  </button>
                </td>
                <td>{clip.status}</td>
                <td>{percent(clip.wer)}</td>
                <td>{percent(clip.cer)}</td>
                <td className="mono-inline">{editCounts(clip)}</td>
                <td>{clip.provider}</td>
                <td className="mono-inline">
                  {rtf(clip.timings.real_time_factor)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {filtered.length === 0 && (
        <p className="hint" data-testid="clips-empty">
          No clips match this search.
        </p>
      )}
      <div className="pager">
        <button
          type="button"
          className="ghost"
          aria-label="Previous page"
          disabled={currentPage <= 1}
          onClick={() => setPage(Math.max(1, currentPage - 1))}
        >
          <CaretLeft size={14} aria-hidden="true" />
          Previous
        </button>
        <span className="hint" data-testid="clip-page">
          Page {currentPage} of {totalPages} · {filtered.length} clip
          {filtered.length === 1 ? "" : "s"}
        </span>
        <button
          type="button"
          className="ghost"
          aria-label="Next page"
          disabled={currentPage >= totalPages}
          onClick={() => setPage(Math.min(totalPages, currentPage + 1))}
        >
          Next
          <CaretRight size={14} aria-hidden="true" />
        </button>
      </div>

      {artifact ? (
        selectedClipId === null ? (
          <p className="hint" data-testid="diff-hint">
            Select a clip above to compare the reference with the transcript.
          </p>
        ) : selectedArtifactClip ? (
          <ClipDiff clip={selectedArtifactClip} />
        ) : (
          <p className="hint" data-testid="clip-missing">
            This clip has no entry in the stored artifact.
          </p>
        )
      ) : (
        <div className="artifact-note" data-testid="artifact-note">
          <WarningCircle size={16} weight="bold" aria-hidden="true" />
          <p className="hint">
            The stored artifact is not available
            {artifactError ? ` — ${artifactError}` : " yet"}. The numbers and
            the clip list above come from the run record; the
            reference-versus-transcript comparison needs the artifact itself,
            and nothing is shown in its place.
          </p>
        </div>
      )}

      <section className="methodology" aria-labelledby="methodology-heading">
        <h4 id="methodology-heading">Methodology</h4>
        <dl className="methodology-facts">
          <div>
            <dt>Dataset</dt>
            <dd>
              {record.dataset_id}@{record.dataset_version}
            </dd>
          </div>
          <div>
            <dt>Provider</dt>
            <dd>
              {record.provider}
              {record.model ? ` (${record.model})` : ""}
            </dd>
          </div>
          <SettingRows label="Decoding" settings={record.decoding} />
          <SettingRows label="Normalization" settings={record.normalization} />
        </dl>
        <p className="hint">
          <strong>A transcription result</strong> is one clip: the text a
          provider heard, its timestamps, and no score at all.{" "}
          <strong>An evaluation run</strong> measures many clips against
          human-verified references, so its scores are only as good as those
          references — and a clip it could not score is reported, never
          averaged in as a zero.
        </p>
      </section>
    </section>
  );
}

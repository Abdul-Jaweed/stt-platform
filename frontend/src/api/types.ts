/**
 * Hand-mirrored types for the backend /v1 contract.
 *
 * Source of truth: backend `schemas/` + docs/api.md. No codegen: the
 * contract is small and stable; drift is caught by the live e2e (T7).
 */
import type { Alignment } from "../lib/alignment";

export type JobStatus =
  | "ACCEPTED"
  | "VALIDATING"
  | "STORED"
  | "QUEUED"
  | "PROCESSING"
  | "RETRY_WAIT"
  | "SUCCEEDED"
  | "FAILED"
  | "CANCELLED";

export const TERMINAL_STATUSES: readonly JobStatus[] = [
  "SUCCEEDED",
  "FAILED",
  "CANCELLED",
];

export function isTerminal(status: JobStatus): boolean {
  return TERMINAL_STATUSES.includes(status);
}

export interface JobFailure {
  code: string;
  message: string;
  retryable: boolean;
  provider_code?: string | null;
}

export interface AudioMetadata {
  format_name: string;
  codec?: string | null;
  sample_rate: number;
  channels: number;
  duration_s: number;
  size_bytes: number;
}

export interface JobRecord {
  id: string;
  scope?: string;
  status: JobStatus;
  requested_provider?: string | null;
  actual_provider?: string | null;
  model?: string | null;
  model_version?: string | null;
  language_requested?: string | null;
  language_detected?: string | null;
  audio?: AudioMetadata | null;
  original_object_key?: string | null;
  attempt_count: number;
  failure?: JobFailure | null;
  options: Record<string, unknown>;
  created_at: string;
  updated_at: string;
}

export interface TranscriptWord {
  start: number;
  end: number;
  text: string;
  confidence?: number | null;
}

export interface TranscriptSegment {
  start: number;
  end: number;
  text: string;
  words?: TranscriptWord[] | null;
}

export interface TranscriptResult {
  job_id: string;
  text: string;
  language?: string | null;
  language_probability?: number | null;
  segments: TranscriptSegment[];
  timestamp_granularity: "word" | "segment";
  audio_duration_s: number;
  processing_duration_s: number;
  provider: string;
  model?: string | null;
  model_version?: string | null;
}

/**
 * A transient preview from `POST /v1/transcriptions/preview`: the text heard
 * so far while recording. Nothing is stored and `is_final` is always false —
 * it is not the transcript, and must never be presented as one.
 */
export interface LivePreview {
  text: string;
  language?: string | null;
  segments: TranscriptSegment[];
  timestamp_granularity: "segment" | "word";
  audio_duration_s: number;
  processing_duration_s: number;
  provider: string;
  model: string;
  model_version?: string | null;
  is_final: false;
}

/**
 * Frames of `WS /v1/transcriptions/stream` (see docs/streaming.md).
 *
 * `partial` is the utterance still open -- it can change. `final` is an
 * utterance the VAD closed and the model decoded exactly once: its text will
 * not change again, only be joined by more. `done` is the end of the session,
 * and `done.text` is the authoritative concatenation.
 */
export interface StreamWindow {
  start_s: number;
  end_s: number;
}

export interface StreamReady {
  type: "ready";
  provider: string;
  model: string;
  session_id: string;
  max_duration_s: number;
  partial_interval_s: number;
  sample_rate: number;
  input_format: "container" | "pcm16";
}

export interface StreamPartial {
  type: "partial";
  text: string;
  segments: TranscriptSegment[];
  window: StreamWindow;
  is_final: false;
}

export interface StreamFinal {
  type: "final";
  text: string;
  segments: TranscriptSegment[];
  window: StreamWindow;
}

export interface StreamErrorFrame {
  type: "error";
  code: string;
  message: string;
  fatal?: boolean;
}

export interface StreamCapped {
  type: "capped";
  max_duration_s: number;
}

export interface StreamDone {
  type: "done";
  reason: string;
  duration_s: number;
  text: string;
}

export type StreamFrame =
  | StreamReady
  | StreamPartial
  | StreamFinal
  | StreamErrorFrame
  | StreamCapped
  | StreamDone;

export interface ProviderCapabilities {
  languages: string[];
  language_detection: boolean;
  segment_timestamps: boolean;
  word_timestamps: boolean;
  diarization: boolean;
  streaming: boolean;
  batch: boolean;
  async_processing: boolean;
  confidence_scores: boolean;
  formats: string[];
  max_file_bytes?: number | null;
  max_duration_s?: number | null;
}

/**
 * The deployment's own caps — distinct from any provider's. Served by
 * `GET /v1/providers` so the UI never re-types a number the backend enforces.
 */
export interface PlatformLimits {
  max_upload_bytes: number;
  max_sync_duration_s: number;
  max_async_duration_s: number;
  max_live_preview_duration_s: number;
}

/** `GET /v1/providers` in full: the directory plus this deployment's caps. */
export interface ProviderDirectory {
  providers: ProviderInfo[];
  limits: PlatformLimits;
}

export interface ProviderHealth {
  status: "ok" | "degraded" | "unavailable" | string;
  detail?: string | null;
}

export interface ProviderInfo {
  id: string;
  name: string;
  is_local: boolean;
  capabilities: ProviderCapabilities;
  health: ProviderHealth;
}

/** Phase 6 contract — the endpoint does not exist yet (honest 404). */
export interface EvaluationClipSummary {
  clip_id: string;
  status: string;
  provider: string;
  model: string | null;
  audio_sha256: string | null;
  wer: number | null;
  cer: number | null;
  word_errors: Record<string, number>;
  char_errors: Record<string, number>;
  timings: Record<string, number | null>;
  timestamps: Record<string, unknown> | null;
  error: string | null;
}

export interface MlflowOutcome {
  status: "logged" | "error" | "disabled" | "skipped";
  run_id?: string;
  run_name?: string;
  experiment?: string;
  tracking_uri?: string;
  reason?: string;
  metrics_logged?: string[];
}

export interface EvaluationRecord {
  evaluation_id: string;
  dataset_id: string;
  dataset_version: string;
  provider: string;
  model: string | null;
  status: string;
  decoding: Record<string, unknown>;
  normalization: Record<string, unknown>;
  aggregate: Record<string, number | null>;
  by_group: Record<string, Record<string, Record<string, number | null>>>;
  insufficient_evidence: boolean;
  started_at: string | null;
  finished_at: string | null;
  duration_s: number;
  mlflow: MlflowOutcome | null;
  artifact_key: string | null;
  clips: EvaluationClipSummary[];
}

export interface EvaluationComparisonRow {
  evaluation_id: string;
  provider: string;
  model: string | null;
  dataset_id: string;
  dataset_version: string;
  clips_scored: number;
  clips_total: number;
  wer_micro: number | null;
  wer_macro: number | null;
  cer_micro: number | null;
  rtf_mean: number | null;
  failures: number;
  segment_availability_mean: number | null;
  word_availability_mean: number | null;
  insufficient_evidence: boolean;
  started_at: string | null;
}

export interface EvaluationList {
  evaluations: EvaluationComparisonRow[];
  count: number;
}

/**
 * One metric's explanation, served from the code that computes it.
 * `computed_by` names the callable the number actually comes from; the text
 * fields say what the number means, where it can mislead, and when it is
 * absent instead of zero.
 */
export interface MetricExplanation {
  name: string;
  applies_to: string;
  unit: string;
  direction: string;
  formula: string;
  inputs: string;
  normalization: string;
  edge_behaviour: string;
  limitations: string;
  computed_by: string;
}

/** `GET /v1/evaluations/metrics`: every metric the platform reports. */
export interface MetricExplanationList {
  metrics: MetricExplanation[];
}

/** The counts behind one per-clip WER/CER measurement. */
export interface EvaluationErrorCounts {
  substitutions: number;
  deletions: number;
  insertions: number;
  errors: number;
  reference_units: number;
  hypothesis_units: number;
}

/**
 * One clip in the stored artifact: the words themselves, onto which the
 * numbers in the record index. `alignment` and `accuracy` are null for a
 * clip that never produced a transcript — there is nothing to compare, and
 * an empty diff would read like an agreement.
 */
export interface EvaluationArtifactClip {
  clip_id: string;
  status: string;
  provider: string;
  model: string | null;
  audio_sha256: string | null;
  transcript_text: string | null;
  reference_text: string | null;
  alignment: Alignment | null;
  accuracy: {
    wer: number | null;
    cer: number | null;
    normalization: Record<string, unknown>;
    word_errors: EvaluationErrorCounts;
    char_errors: EvaluationErrorCounts;
  } | null;
  timestamps: Record<string, unknown> | null;
  timings: Record<string, number | null>;
  attempts: number;
  retries: number;
  error: string | null;
}

/**
 * `GET /v1/evaluations/{id}/artifact`: the words behind a run's numbers.
 * Answers 404 ARTIFACT_NOT_FOUND when the run stored none, and 503
 * STORAGE_NOT_CONFIGURED when the deployment has no storage at all.
 */
export interface EvaluationArtifact {
  dataset: { dataset_id: string; version: string };
  provider: string;
  model: string | null;
  decoding: Record<string, unknown>;
  normalization: Record<string, unknown>;
  clips: EvaluationArtifactClip[];
  aggregate: Record<string, number | null>;
  by_group: Record<string, Record<string, Record<string, number | null>>>;
  insufficient_evidence: boolean;
  started_at: string | null;
  finished_at: string | null;
  duration_s: number;
  mlflow: MlflowOutcome | null;
}

export interface EvaluationRequest {
  dataset_id: string;
  provider?: string;
  language?: string;
}

export interface SessionInfo {
  authenticated: boolean;
  username: string | null;
  tenant: string;
  /** Whether the server requires a login at all (STT_UI_AUTH_ENABLED). */
  ui_auth_enabled: boolean;
  expires_at: number | null;
}

/** `GET /v1/auth/config`: two booleans, no secrets. */
export interface AuthConfig {
  ui_auth_enabled: boolean;
  dev_credentials: boolean;
  /** The login page's demo panel. Off in production, whatever the setting. */
  demo_mode: boolean;
  demo_username: string | null;
  /** Only ever the published development default — never a generated secret. */
  demo_password: string | null;
}

export interface LoginResponse {
  username: string;
  tenant: string;
  expires_at: number;
  token: string;
}

export interface EvaluationDatasetInfo {
  dataset_id: string;
  version: string;
  language: string;
  description: string | null;
  clips_total: number;
  clips_scored: number;
  clips_unscored: number;
  audio_ready: boolean;
  clips_missing_audio: number;
  load_error: string | null;
}

export interface EvaluationDatasetList {
  datasets: EvaluationDatasetInfo[];
}

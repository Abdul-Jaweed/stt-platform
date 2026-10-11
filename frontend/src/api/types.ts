/**
 * Hand-mirrored types for the backend /v1 contract.
 *
 * Source of truth: backend `schemas/` + docs/api.md. No codegen: the
 * contract is small and stable; drift is caught by the live e2e (T7).
 */

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

export interface EvaluationRequest {
  dataset_id: string;
  provider?: string;
  language?: string;
}

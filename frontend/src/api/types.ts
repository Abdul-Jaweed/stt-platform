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
export interface EvaluationRecord {
  evaluation_id: string;
  [key: string]: unknown;
}

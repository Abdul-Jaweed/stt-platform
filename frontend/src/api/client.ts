import { ApiError, normalizeError } from "./errors";
import type {
  EvaluationRecord,
  JobRecord,
  ProviderInfo,
  TranscriptResult,
} from "./types";

export interface CreateTranscriptionInput {
  file: File;
  provider?: string;
  language?: string;
  idempotencyKey?: string;
}

export interface CreateTranscriptionResponse {
  /** 201: terminal sync job. 202: async job accepted (poll it). */
  status: 201 | 202;
  job: JobRecord;
}

export interface ApiClient {
  createTranscription(
    input: CreateTranscriptionInput,
  ): Promise<CreateTranscriptionResponse>;
  getJob(id: string): Promise<JobRecord>;
  getResult(id: string): Promise<TranscriptResult>;
  retry(id: string): Promise<JobRecord>;
  cancel(id: string): Promise<JobRecord>;
  getProviders(): Promise<ProviderInfo[]>;
  getHealth(): Promise<{ status: string }>;
  getEvaluation(id: string): Promise<EvaluationRecord>;
}

async function readBody(response: Response): Promise<unknown> {
  const text = await response.text();
  if (!text) return null;
  try {
    return JSON.parse(text);
  } catch {
    return null;
  }
}

export function createApiClient(baseUrl: string): ApiClient {
  const url = (path: string) => `${baseUrl.replace(/\/+$/, "")}${path}`;

  async function call(path: string, init?: RequestInit): Promise<Response> {
    try {
      return await fetch(url(path), init);
    } catch (cause) {
      throw new ApiError(
        0,
        "NETWORK_ERROR",
        `could not reach the API at ${url(path)} (${String(cause)})`,
      );
    }
  }

  async function get<T>(path: string): Promise<T> {
    const response = await call(path);
    const body = await readBody(response);
    if (!response.ok) throw normalizeError(response.status, body);
    return body as T;
  }

  async function post<T>(path: string): Promise<T> {
    const response = await call(path, { method: "POST" });
    const body = await readBody(response);
    if (!response.ok) throw normalizeError(response.status, body);
    return body as T;
  }

  const jobPath = (id: string) =>
    `/v1/transcriptions/${encodeURIComponent(id)}`;

  return {
    async createTranscription(input) {
      const form = new FormData();
      form.append("file", input.file);
      if (input.provider) form.append("provider", input.provider);
      if (input.language) form.append("language", input.language);
      const headers: Record<string, string> = {};
      if (input.idempotencyKey) headers["Idempotency-Key"] = input.idempotencyKey;

      const response = await call("/v1/transcriptions", {
        method: "POST",
        body: form,
        headers,
      });
      const body = await readBody(response);
      if (!response.ok) throw normalizeError(response.status, body);
      return { status: response.status as 201 | 202, job: body as JobRecord };
    },

    getJob: (id) => get<JobRecord>(jobPath(id)),

    getResult: (id) => get<TranscriptResult>(`${jobPath(id)}/result`),

    retry: (id) => post<JobRecord>(`${jobPath(id)}/retry`),

    cancel: (id) => post<JobRecord>(`${jobPath(id)}/cancel`),

    async getProviders() {
      const data = await get<{ providers: ProviderInfo[] }>("/v1/providers");
      return data.providers;
    },

    getHealth: () => get<{ status: string }>("/v1/health/ready"),

    getEvaluation: (id) =>
      get<EvaluationRecord>(`/v1/evaluations/${encodeURIComponent(id)}`),
  };
}

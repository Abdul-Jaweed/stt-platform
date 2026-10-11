import { ApiError, normalizeError } from "./errors";
import type {
  AuthConfig,
  EvaluationList,
  LivePreview,
  EvaluationRecord,
  EvaluationRequest,
  JobRecord,
  LoginResponse,
  ProviderInfo,
  SessionInfo,
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

export interface PreviewInput {
  /** The audio captured so far; a File from a recorder or any Blob. */
  blob: Blob;
  filename?: string;
  language?: string;
  provider?: string;
  signal?: AbortSignal;
}

export interface ApiClient {
  createTranscription(
    input: CreateTranscriptionInput,
  ): Promise<CreateTranscriptionResponse>;
  /** Transient live preview: local provider only, nothing persisted. */
  previewTranscription(input: PreviewInput): Promise<LivePreview>;
  getJob(id: string): Promise<JobRecord>;
  getResult(id: string): Promise<TranscriptResult>;
  retry(id: string): Promise<JobRecord>;
  cancel(id: string): Promise<JobRecord>;
  getProviders(): Promise<ProviderInfo[]>;
  getHealth(): Promise<{ status: string }>;
  /** Who the caller is; a 401 means the server wants a login. */
  getSession(): Promise<SessionInfo>;
  /** Whether login is required, and whether the dev defaults are in force. */
  getAuthConfig(): Promise<AuthConfig>;
  login(username: string, password: string): Promise<LoginResponse>;
  logout(): Promise<{ ok: boolean }>;
  getEvaluation(id: string): Promise<EvaluationRecord>;
  listEvaluations(): Promise<EvaluationList>;
  createEvaluation(input: EvaluationRequest): Promise<EvaluationRecord>;
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
      // The session cookie is the browser's only credential: it must travel
      // with every call, including cross-origin during local development.
      return await fetch(url(path), { credentials: "include", ...init });
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

  async function postJson<T>(path: string, payload: unknown): Promise<T> {
    const response = await call(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
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

    async previewTranscription(input) {
      const form = new FormData();
      form.append("file", input.blob, input.filename ?? "preview.webm");
      if (input.provider) form.append("provider", input.provider);
      if (input.language) form.append("language", input.language);

      const response = await call("/v1/transcriptions/preview", {
        method: "POST",
        body: form,
        signal: input.signal,
      });
      const body = await readBody(response);
      if (!response.ok) throw normalizeError(response.status, body);
      return body as LivePreview;
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

    getSession: () => get<SessionInfo>("/v1/auth/me"),
    getAuthConfig: () => get<AuthConfig>("/v1/auth/config"),

    login: (username, password) =>
      postJson<LoginResponse>("/v1/auth/login", { username, password }),

    logout: () => post<{ ok: boolean }>("/v1/auth/logout"),

    getEvaluation: (id) =>
      get<EvaluationRecord>(`/v1/evaluations/${encodeURIComponent(id)}`),

    listEvaluations: () => get<EvaluationList>("/v1/evaluations"),

    createEvaluation: (input) =>
      postJson<EvaluationRecord>("/v1/evaluations", input),
  };
}

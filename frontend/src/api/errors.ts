/**
 * Normalized API errors. The backend's error shape is always
 * ``{"error": {"code", "message"}}``; anything else degrades to
 * UNKNOWN_ERROR without inventing a message.
 */

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;

  constructor(status: number, code: string, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
  }
}

interface ErrorBody {
  error?: { code?: string; message?: string };
}

export function normalizeError(status: number, body: unknown): ApiError {
  const err = (body as ErrorBody | null | undefined)?.error;
  const code = typeof err?.code === "string" ? err.code : "UNKNOWN_ERROR";
  const message =
    typeof err?.message === "string"
      ? err.message
      : `request failed with status ${status}`;
  return new ApiError(status, code, message);
}

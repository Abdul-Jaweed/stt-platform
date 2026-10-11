import type { JobStatus } from "../api/types";

const TONE: Record<JobStatus, string> = {
  ACCEPTED: "badge-neutral",
  VALIDATING: "badge-neutral",
  STORED: "badge-neutral",
  QUEUED: "badge-info",
  PROCESSING: "badge-info",
  RETRY_WAIT: "badge-warn",
  SUCCEEDED: "badge-ok",
  FAILED: "badge-error",
  CANCELLED: "badge-muted",
};

export default function StatusBadge({ status }: { status: JobStatus }) {
  return <span className={`badge ${TONE[status]}`}>{status}</span>;
}

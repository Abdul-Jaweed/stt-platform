import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";

import { useApi } from "../api/context";
import { ApiError } from "../api/errors";
import { isTerminal } from "../api/types";
import type { JobRecord } from "../api/types";
import JobStages from "../components/JobStages";
import StatusBadge from "../components/StatusBadge";
import { useJobPolling } from "../hooks/useJobPolling";
import { formatBytes, formatDuration } from "../lib/format";
import { addRecentJob } from "../lib/recentJobs";

interface ActionError {
  code: string;
  message: string;
}

export default function JobPage({
  pollIntervalMs = 2000,
}: {
  pollIntervalMs?: number;
}) {
  const { id } = useParams();
  const api = useApi();
  const jobId = id ?? "";
  const { job, error, loading, refresh } = useJobPolling(
    api,
    jobId,
    pollIntervalMs,
  );

  const [acting, setActing] = useState(false);
  const [actionError, setActionError] = useState<ActionError | null>(null);

  useEffect(() => {
    if (job) {
      addRecentJob({ id: job.id, createdAt: job.created_at });
    }
  }, [job]);

  async function runAction(action: (jobId: string) => Promise<JobRecord>) {
    if (acting) return;
    setActing(true);
    setActionError(null);
    try {
      await action(jobId);
      refresh();
    } catch (err) {
      setActionError(
        err instanceof ApiError
          ? { code: err.code, message: err.message }
          : { code: "UNKNOWN_ERROR", message: String(err) },
      );
    } finally {
      setActing(false);
    }
  }

  if (loading && !job && !error) {
    return <p className="hint">Loading job…</p>;
  }

  if (error && !job) {
    return (
      <section className="page job-page">
        <h2>Job {jobId}</h2>
        <p role="alert" className="error">
          <strong>{error.code}</strong> {error.message}
        </p>
      </section>
    );
  }

  if (!job) return null;

  const cancellable = !isTerminal(job.status) && job.status !== "SUCCEEDED";

  return (
    <section className="page job-page">
      <header className="page-head">
        <h2>
          Job <code className="job-id">{job.id}</code>{" "}
          <StatusBadge status={job.status} />
        </h2>
      </header>

      <JobStages status={job.status} />

      <div className="card">
        <dl className="job-meta">
        <dt>Provider</dt>
        <dd>{job.actual_provider ?? job.requested_provider ?? "—"}</dd>
        <dt>Model</dt>
        <dd>{job.model ?? "—"}</dd>
        <dt>Language</dt>
        <dd>{job.language_detected ?? job.language_requested ?? "auto"}</dd>
        <dt>Attempts</dt>
        <dd>{job.attempt_count}</dd>
        <dt>Created</dt>
        <dd>{job.created_at}</dd>
        {job.audio && (
          <>
            <dt>Audio</dt>
            <dd>
              {job.audio.format_name} — {formatDuration(job.audio.duration_s)},
              {" "}
              {formatBytes(job.audio.size_bytes)}
            </dd>
          </>
        )}
        </dl>
      </div>

      {job.failure && (
        <p role="alert" className="error">
          <strong>{job.failure.code}</strong> {job.failure.message}
        </p>
      )}

      {actionError && (
        <p role="alert" className="error">
          <strong>{actionError.code}</strong> {actionError.message}
        </p>
      )}

      <div className="actions">
        {job.status === "SUCCEEDED" && (
          <Link className="button primary" to={`/jobs/${job.id}/transcript`}>
            View transcript
          </Link>
        )}
        {job.failure?.retryable && (
          <button
            type="button"
            disabled={acting}
            onClick={() => void runAction((jid) => api.retry(jid))}
          >
            Retry
          </button>
        )}
        {cancellable && (
          <button
            type="button"
            disabled={acting}
            onClick={() => void runAction((jid) => api.cancel(jid))}
          >
            Cancel
          </button>
        )}
        <button type="button" onClick={refresh}>
          Refresh
        </button>
      </div>
    </section>
  );
}

import type { JobStatus } from "../api/types";

const STEPS: { key: string; label: string; statuses: JobStatus[] }[] = [
  { key: "received", label: "Received", statuses: ["ACCEPTED"] },
  { key: "validated", label: "Validated", statuses: ["VALIDATING"] },
  { key: "stored", label: "Stored", statuses: ["STORED"] },
  { key: "queued", label: "Queued", statuses: ["QUEUED"] },
  {
    key: "transcribing",
    label: "Transcribing",
    statuses: ["PROCESSING", "RETRY_WAIT"],
  },
  { key: "done", label: "Done", statuses: ["SUCCEEDED"] },
];

const STOPPED: JobStatus[] = ["FAILED", "CANCELLED"];

export default function JobStages({ status }: { status: JobStatus }) {
  const stopped = STOPPED.includes(status);
  const currentIndex = STEPS.findIndex((step) => step.statuses.includes(status));

  return (
    <ol className="stages" aria-label="Job progress">
      {STEPS.map((step, index) => {
        // A finished job is done everywhere: "current" would suggest work in
        // progress. A stopped job claims no position at all.
        const state = stopped
          ? "neutral"
          : status === "SUCCEEDED"
            ? "done"
            : index < currentIndex
              ? "done"
              : index === currentIndex
                ? "current"
                : "todo";
        const retrying = step.key === "transcribing" && status === "RETRY_WAIT";
        return (
          <li
            key={step.key}
            className={`stage is-${state}`}
            data-stage={step.key}
            aria-current={state === "current" ? "step" : undefined}
          >
            <span className="stage-dot" aria-hidden="true" />
            <span className="stage-label">
              {step.label}
              {retrying && <> <span className="stage-note">retrying</span></>}
            </span>
          </li>
        );
      })}
    </ol>
  );
}

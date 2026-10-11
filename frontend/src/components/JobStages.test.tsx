/**
 * Where a job is in its lifecycle, without inventing information.
 *
 * A running job has a precise position. A failed or cancelled job does *not*:
 * the platform knows the last committed status, not the stage that broke, so
 * the tracker goes neutral and the failure panel explains it. Faking a
 * position would be the kind of confidence the spec forbids.
 */
import { render } from "@testing-library/react";
import { describe, expect, test } from "vitest";

import type { JobStatus } from "../api/types";
import JobStages from "./JobStages";

function currentStage(): string | null {
  const node = document.querySelector('[aria-current="step"]');
  return node ? (node.textContent ?? "") : null;
}

function stages(status: JobStatus) {
  render(<JobStages status={status} />);
  return Array.from(document.querySelectorAll("li.stage")).map((node) => ({
    label: node.querySelector(".stage-label")?.textContent ?? "",
    state: Array.from(node.classList)
      .find((name) => name.startsWith("is-"))
      ?.replace("is-", ""),
  }));
}

describe("JobStages", () => {
  test("marks the current stage for a running job", () => {
    const rendered = stages("PROCESSING");

    expect(currentStage()).toBe("Transcribing");
    const transcribing = rendered.find((s) => s.label === "Transcribing");
    expect(transcribing?.state).toBe("current");
    expect(rendered.filter((s) => s.state === "done").map((s) => s.label)).toEqual([
      "Received",
      "Validated",
      "Stored",
      "Queued",
    ]);
  });

  test("a queued job sits at Queued", () => {
    stages("QUEUED");
    expect(currentStage()).toBe("Queued");
  });

  test("a succeeded job is done everywhere and current nowhere", () => {
    const rendered = stages("SUCCEEDED");

    expect(currentStage()).toBeNull();
    expect(rendered.every((s) => s.state === "done")).toBe(true);
  });

  test("retrying is stated on the transcribing stage", () => {
    stages("RETRY_WAIT");
    expect(currentStage()).toBe("Transcribing retrying");
  });

  test("a failed job claims no position", () => {
    const rendered = stages("FAILED");

    expect(currentStage()).toBeNull();
    expect(rendered.every((s) => s.state === "neutral")).toBe(true);
  });

  test("a cancelled job claims no position either", () => {
    stages("CANCELLED");
    expect(currentStage()).toBeNull();
  });
});

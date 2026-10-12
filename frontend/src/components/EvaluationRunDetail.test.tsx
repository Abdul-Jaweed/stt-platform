/**
 * The run detail: the numbers, the words behind them, and what they mean.
 *
 * Every assertion here is a promise the console makes: that a value is the
 * server's own, that its explanation is the explanation the code serving it
 * carries, that a measurement nobody took says so — and that the diff a
 * reader sees comes off the same alignment the run scored with.
 */
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, test, vi } from "vitest";

import type {
  EvaluationArtifact,
  EvaluationArtifactClip,
  EvaluationClipSummary,
  EvaluationRecord,
  MetricExplanation,
} from "../api/types";
import EvaluationRunDetail from "./EvaluationRunDetail";

const ALPHABET = { equal: "M", substitute: "S", delete: "D", insert: "I" };

function clipSummary(
  clip_id: string,
  overrides: Partial<EvaluationClipSummary> = {},
): EvaluationClipSummary {
  return {
    clip_id,
    status: "scored",
    provider: "faster-whisper",
    model: "large-v3-turbo",
    audio_sha256: "abc",
    wer: 0.1,
    cer: 0.05,
    word_errors: { substitutions: 1, deletions: 0, insertions: 0 },
    char_errors: { substitutions: 1, deletions: 0, insertions: 0 },
    timings: { processing_s: 0.9, real_time_factor: 0.07 },
    timestamps: null,
    error: null,
    ...overrides,
  };
}

function artifactClip(
  clip_id: string,
  overrides: Partial<EvaluationArtifactClip> = {},
): EvaluationArtifactClip {
  return {
    clip_id,
    status: "scored",
    provider: "faster-whisper",
    model: "large-v3-turbo",
    audio_sha256: "abc",
    transcript_text: "the quik brown jumps",
    reference_text: "the quick brown fox",
    alignment: {
      code: "MSMDI",
      alphabet: ALPHABET,
      reference_tokens: ["the", "quick", "brown", "fox"],
      hypothesis_tokens: ["the", "quik", "brown", "jumps"],
    },
    accuracy: {
      wer: 0.25,
      cer: 0.1,
      normalization: { name: "stt-normalize-v1" },
      word_errors: {
        substitutions: 1,
        deletions: 1,
        insertions: 1,
        errors: 3,
        reference_units: 4,
        hypothesis_units: 4,
      },
      char_errors: {
        substitutions: 0,
        deletions: 0,
        insertions: 0,
        errors: 0,
        reference_units: 1,
        hypothesis_units: 1,
      },
    },
    timestamps: null,
    timings: { processing_s: 0.9, real_time_factor: 0.07 },
    attempts: 1,
    retries: 0,
    error: null,
    ...overrides,
  };
}

function record(overrides: Partial<EvaluationRecord> = {}): EvaluationRecord {
  return {
    evaluation_id: "eval-1",
    dataset_id: "stt-eval-core",
    dataset_version: "1.0.0",
    provider: "faster-whisper",
    model: "large-v3-turbo",
    status: "COMPLETED",
    decoding: { language: "en", beam_size: 5 },
    normalization: {
      name: "stt-normalize-v1",
      nfkc: true,
      lowercase: true,
      strip_punctuation: true,
    },
    aggregate: { clips_scored: 1, clips_total: 1, wer_micro: 0.1, failures: 0 },
    by_group: {},
    insufficient_evidence: false,
    started_at: "2026-10-11T20:00:00Z",
    finished_at: "2026-10-11T20:00:12Z",
    duration_s: 12.0,
    mlflow: null,
    artifact_key: "artifacts/evaluation/eval-1/result.json",
    clips: [clipSummary("syn-en-tech-001")],
    ...overrides,
  };
}

function artifact(overrides: Partial<EvaluationArtifact> = {}): EvaluationArtifact {
  return {
    dataset: { dataset_id: "stt-eval-core", version: "1.0.0" },
    provider: "faster-whisper",
    model: "large-v3-turbo",
    decoding: { language: "en", beam_size: 5 },
    normalization: { name: "stt-normalize-v1" },
    clips: [artifactClip("syn-en-tech-001")],
    aggregate: {
      wer_micro: 0.25,
      wer_macro: 0.3,
      cer_micro: 0.2,
      cer_macro: 0.22,
      failure_rate: 0.0,
      rtf_mean: 0.072,
      coverage_mean: 1.0,
      clips_scored: 1,
      clips_total: 1,
    },
    by_group: {},
    insufficient_evidence: false,
    started_at: "2026-10-11T20:00:00",
    finished_at: "2026-10-11T20:00:12",
    duration_s: 12.0,
    mlflow: null,
    ...overrides,
  };
}

function explanation(
  overrides: Partial<MetricExplanation> = {},
): MetricExplanation {
  return {
    name: "wer_micro",
    applies_to: "run aggregate",
    unit: "ratio — total word errors over total reference words",
    direction: "lower is better",
    formula:
      "sum(errors over scored clips) / sum(reference words over scored clips)",
    inputs: "every clip the run scored",
    normalization: "stt-normalize-v1",
    edge_behaviour: "null when the run scored no clips with a reference",
    limitations: "long clips dominate",
    computed_by: "wer_micro",
    ...overrides,
  };
}

function renderDetail(
  props: Partial<Parameters<typeof EvaluationRunDetail>[0]> = {},
) {
  return render(
    <EvaluationRunDetail
      record={record()}
      artifact={null}
      artifactError={null}
      metrics={[]}
      {...props}
    />,
  );
}

describe("EvaluationRunDetail", () => {
  test("the header names the run, what ran it, and how long it took", async () => {
    const user = userEvent.setup();
    const writeText = vi.fn().mockResolvedValue(undefined);
    // jsdom has no clipboard; the button's promise is the same one a browser
    // would keep, so give it a surface rather than weakening the component.
    Object.defineProperty(navigator, "clipboard", {
      value: { writeText },
      configurable: true,
    });

    renderDetail({
      record: record({
        mlflow: {
          status: "logged",
          run_id: "run-123",
          experiment: "stt-evaluation",
          tracking_uri: "http://127.0.0.1:5000",
        },
      }),
    });

    const runId = screen.getByText("eval-1");
    expect(runId.tagName).toBe("CODE");

    const subhead = screen.getByText(/stt-eval-core@1\.0\.0/, {
      selector: "p.detail-subhead",
    });
    expect(subhead).toHaveTextContent("faster-whisper");
    expect(subhead).toHaveTextContent("large-v3-turbo");
    expect(subhead).toHaveTextContent("COMPLETED");
    expect(subhead).toHaveTextContent(/started/i);
    expect(subhead).toHaveTextContent(/finished/i);
    expect(subhead).toHaveTextContent(/ran for 12\.0s/);

    expect(screen.getByTestId("mlflow-status")).toHaveTextContent(/run-123/);

    await user.click(screen.getByRole("button", { name: /copy evaluation id/i }));
    expect(writeText).toHaveBeenCalledWith("eval-1");
    expect(
      screen.getByRole("button", { name: /copy evaluation id/i }),
    ).toHaveTextContent(/copied/i);
  });

  test("a metric card carries the server's own explanation, on demand", async () => {
    const user = userEvent.setup();
    const { unmount } = renderDetail({
      record: record({ aggregate: { wer_micro: 0.1 } }),
      artifact: artifact(),
      metrics: [explanation()],
    });

    // The artifact's own aggregate is the run's record of itself: prefer it.
    const card = screen.getByTestId("metric-card-wer_micro");
    expect(within(card).getByTestId("metric-value-wer_micro")).toHaveTextContent(
      "25.0%",
    );

    await user.click(within(card).getByText(/what this number means/i));
    expect(
      within(card).getByText(
        "sum(errors over scored clips) / sum(reference words over scored clips)",
      ),
    ).toBeInTheDocument();
    expect(within(card).getByText("run aggregate")).toBeInTheDocument();
    expect(
      within(card).getByText("null when the run scored no clips with a reference"),
    ).toBeInTheDocument();
    expect(within(card).getByText("long clips dominate")).toBeInTheDocument();
    // The callable the number actually comes from, as served.
    expect(within(card).getAllByText("wer_micro").length).toBeGreaterThanOrEqual(2);

    unmount();

    // Without the artifact the record's aggregate is all there is.
    renderDetail({
      record: record({ aggregate: { wer_micro: 0.1 } }),
      metrics: [explanation()],
    });
    expect(
      screen.getByTestId("metric-value-wer_micro"),
    ).toHaveTextContent("10.0%");
  });

  test("a metric nobody measured says not measured, never zero", () => {
    const { unmount } = renderDetail({
      record: record({ aggregate: { rtf_mean: null } }),
    });
    const card = screen.getByTestId("metric-card-rtf_mean");
    expect(within(card).getByTestId("metric-value-rtf_mean")).toHaveTextContent(
      "—",
    );
    expect(card).toHaveTextContent(/not measured/i);
    expect(card).not.toHaveTextContent(/0/);

    unmount();

    renderDetail({
      record: record({ aggregate: { rtf_mean: 0.072 } }),
    });
    expect(
      screen.getByTestId("metric-value-rtf_mean"),
    ).toHaveTextContent("×0.072");
  });

  test("a headline number with no explanation is flagged, not explained away", () => {
    renderDetail({
      record: record({ aggregate: { coverage_mean: 0.5, wer_micro: 0.1 } }),
      metrics: [explanation()],
    });

    const card = screen.getByTestId("metric-card-coverage_mean");
    expect(within(card).getByTestId("metric-value-coverage_mean")).toHaveTextContent(
      "50.0%",
    );
    expect(
      within(card).getByTestId("metric-unexplained-coverage_mean"),
    ).toHaveTextContent(/no explanation/i);
    // No invented formula for a number nobody explained.
    expect(card.querySelector("details")).toBeNull();

    // The metric that does have an explanation keeps its disclosure.
    expect(
      screen.getByTestId("metric-card-wer_micro").querySelector("details"),
    ).not.toBeNull();
  });

  test("the diff highlights equal, substituted, deleted and inserted tokens", async () => {
    const user = userEvent.setup();
    renderDetail({
      record: record({ clips: [clipSummary("syn-en-tech-001")] }),
      artifact: artifact({ clips: [artifactClip("syn-en-tech-001")] }),
    });

    await user.click(screen.getByRole("button", { name: "syn-en-tech-001" }));

    const reference = screen.getByTestId("diff-reference");
    expect(reference.querySelectorAll(".diff-token.diff-equal")).toHaveLength(2);
    expect(reference.querySelectorAll(".diff-substitute")).toHaveLength(1);
    expect(reference.querySelectorAll(".diff-delete")).toHaveLength(1);

    const hypothesis = screen.getByTestId("diff-hypothesis");
    expect(hypothesis.querySelectorAll(".diff-equal")).toHaveLength(2);
    expect(hypothesis.querySelectorAll(".diff-substitute")).toHaveLength(1);
    expect(hypothesis.querySelectorAll(".diff-insert")).toHaveLength(1);
    expect(hypothesis.querySelectorAll(".diff-delete")).toHaveLength(0);

    // The words are the words: none dropped, none invented.
    expect(reference).toHaveTextContent("fox");
    expect(hypothesis).toHaveTextContent("jumps");

    const counts = screen.getByTestId("diff-counts");
    expect(counts).toHaveTextContent("substitutions 1");
    expect(counts).toHaveTextContent("deletions 1");
    expect(counts).toHaveTextContent("insertions 1");

    const legend = screen.getByRole("list", { name: /diff legend/i });
    expect(within(legend).getByText("equal")).toBeInTheDocument();
    expect(within(legend).getByText("substitution")).toBeInTheDocument();
    expect(within(legend).getByText("deletion")).toBeInTheDocument();
    expect(within(legend).getByText("insertion")).toBeInTheDocument();
  });

  test("a clip that produced no transcript says so instead of faking a diff", async () => {
    const user = userEvent.setup();
    renderDetail({
      record: record({
        clips: [clipSummary("syn-en-fail-001", { status: "failed", wer: null, cer: null })],
      }),
      artifact: artifact({
        clips: [
          artifactClip("syn-en-fail-001", {
            status: "failed",
            transcript_text: null,
            alignment: null,
            accuracy: null,
            error: "RuntimeError: provider timed out",
          }),
        ],
      }),
    });

    await user.click(screen.getByRole("button", { name: "syn-en-fail-001" }));

    const note = screen.getByTestId("no-transcript-note");
    expect(note).toHaveTextContent(/produced no transcript/i);
    expect(note).toHaveTextContent(/nothing to compare/i);
    expect(screen.queryByTestId("diff-reference")).not.toBeInTheDocument();
  });

  test("the clip table searches, sorts and pages the run's clips", async () => {
    const user = userEvent.setup();
    // 23 clips: ten to a page, and clip-23 is deliberately the cleanest so a
    // sort has something to find.
    const clips = Array.from({ length: 23 }, (_, index) => {
      const n = index + 1;
      const wer = n === 23 ? 0.001 : n / 100;
      return clipSummary(`clip-${String(n).padStart(2, "0")}`, {
        wer,
        cer: wer / 2,
      });
    });
    renderDetail({ record: record({ clips }) });

    const table = screen.getByRole("table", { name: /clips/i });
    const bodyRows = () => within(table).getAllByRole("row");

    expect(bodyRows()).toHaveLength(11); // header + 10
    expect(screen.getByTestId("clip-page")).toHaveTextContent("Page 1 of 3");
    expect(within(bodyRows()[1]).getByText("clip-01")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /next page/i }));
    expect(screen.getByTestId("clip-page")).toHaveTextContent("Page 2 of 3");
    expect(within(bodyRows()[1]).getByText("clip-11")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /previous page/i }));
    expect(screen.getByTestId("clip-page")).toHaveTextContent("Page 1 of 3");

    // Search narrows to what it matches and starts over at page one.
    await user.click(screen.getByRole("button", { name: /next page/i }));
    await user.type(screen.getByLabelText(/search clips/i), "clip-23");
    expect(within(table).getAllByRole("row")).toHaveLength(2);
    expect(screen.getByTestId("clip-page")).toHaveTextContent("Page 1 of 1");
    expect(within(table).getByText("clip-23")).toBeInTheDocument();

    await user.clear(screen.getByLabelText(/search clips/i));

    // Sorting by a column reorders, and assistive tech is told which way.
    const werHeader = within(table).getByRole("columnheader", { name: /wer/i });
    await user.click(within(werHeader).getByRole("button"));
    expect(werHeader).toHaveAttribute("aria-sort", "ascending");
    expect(within(bodyRows()[1]).getByText("clip-23")).toBeInTheDocument();
  });

  test("the methodology panel separates a transcription result from a run", () => {
    renderDetail();

    const methodology = screen.getByRole("region", { name: /methodology/i });
    expect(within(methodology).getByText(/stt-normalize-v1/)).toBeInTheDocument();
    expect(within(methodology).getByText("Decoding: language")).toBeInTheDocument();
    expect(within(methodology).getByText(/stt-eval-core@1\.0\.0/)).toBeInTheDocument();

    // The distinction the whole page turns on.
    expect(screen.getByText(/transcription result/i)).toBeInTheDocument();
    expect(screen.getByText(/evaluation run/i)).toBeInTheDocument();
    expect(screen.getByText(/human-verified references/i)).toBeInTheDocument();
  });

  test("an unavailable artifact is stated, and no diff is pretended", async () => {
    const user = userEvent.setup();
    renderDetail({
      artifact: null,
      artifactError: "ARTIFACT_NOT_FOUND: evaluation 'eval-1' stored no artifact",
    });

    const note = screen.getByTestId("artifact-note");
    expect(note).toHaveTextContent(/ARTIFACT_NOT_FOUND/);
    expect(note).toHaveTextContent(/not available/i);

    await user.click(screen.getByRole("button", { name: "syn-en-tech-001" }));
    expect(screen.queryByTestId("diff-reference")).not.toBeInTheDocument();
    expect(screen.queryByTestId("no-transcript-note")).not.toBeInTheDocument();
  });

  test("insufficient evidence is flagged in the current wording", () => {
    renderDetail({ record: record({ insufficient_evidence: true }) });

    expect(screen.getByTestId("insufficient-note")).toHaveTextContent(
      /insufficient evidence to rank/i,
    );
  });
});

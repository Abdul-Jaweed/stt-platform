import { render, screen } from "@testing-library/react";

import type { EvaluationComparisonRow } from "../api/types";
import WerTrend from "./WerTrend";

const row = (
  overrides: Partial<EvaluationComparisonRow> = {},
): EvaluationComparisonRow => ({
  evaluation_id: "eval-1",
  provider: "faster-whisper",
  model: "large-v3-turbo",
  dataset_id: "stt-eval-core",
  dataset_version: "1.1.0",
  clips_scored: 20,
  clips_total: 21,
  wer_micro: 0.1,
  wer_macro: 0.11,
  cer_micro: 0.09,
  rtf_mean: 0.07,
  failures: 0,
  segment_availability_mean: 1,
  word_availability_mean: 1,
  insufficient_evidence: false,
  started_at: "2026-10-11T20:00:00Z",
  ...overrides,
});

test("draws one point per measured run, and the y axis knows worse is higher", () => {
  render(
    <WerTrend
      runs={[
        row({
          evaluation_id: "old",
          wer_micro: 0.3,
          started_at: "2026-10-10T00:00:00Z",
        }),
        row({
          evaluation_id: "mid",
          wer_micro: 0.1,
          started_at: "2026-10-11T00:00:00Z",
        }),
        row({
          evaluation_id: "new",
          wer_micro: 0.2,
          started_at: "2026-10-12T00:00:00Z",
        }),
      ]}
    />,
  );

  const chart = screen.getByRole("img", {
    name: /wer trend over the last 3 measured runs/i,
  });
  const circles = chart.querySelectorAll("circle");
  expect(circles).toHaveLength(3);
  expect(chart.querySelector("polyline")).not.toBeNull();

  // Points run oldest to newest, and a worse WER sits higher on the chart.
  const ys = Array.from(circles).map((circle) =>
    Number(circle.getAttribute("cy")),
  );
  expect(ys[0]).toBeLessThan(ys[1]); // 0.30%... worse than 10%
  expect(ys[2]).toBeLessThan(ys[1]); // 20% worse than 10%
  const xs = Array.from(circles).map((circle) =>
    Number(circle.getAttribute("cx")),
  );
  expect(xs[0]).toBeLessThan(xs[1]);
  expect(xs[1]).toBeLessThan(xs[2]);
});

test("skips runs whose WER was never measured", () => {
  render(
    <WerTrend
      runs={[
        row({
          evaluation_id: "unmeasured",
          wer_micro: null,
          started_at: "2026-10-10T00:00:00Z",
        }),
        row({ evaluation_id: "a", started_at: "2026-10-11T00:00:00Z" }),
        row({ evaluation_id: "b", started_at: "2026-10-12T00:00:00Z" }),
      ]}
    />,
  );

  const chart = screen.getByRole("img", {
    name: /wer trend over the last 2 measured runs/i,
  });
  expect(chart.querySelectorAll("circle")).toHaveLength(2);
});

test("one run is not a trend, and the component says so", () => {
  render(<WerTrend runs={[row({})]} />);

  expect(screen.getByTestId("wer-trend-note")).toHaveTextContent(
    /two measured runs/i,
  );
  expect(screen.queryByRole("img")).not.toBeInTheDocument();
});

test("an empty history asks for a first run rather than drawing nothing", () => {
  render(<WerTrend runs={[]} />);

  expect(screen.getByTestId("wer-trend-note")).toHaveTextContent(
    /none are on record/i,
  );
});

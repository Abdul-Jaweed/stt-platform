import type { EvaluationComparisonRow } from "../api/types";

const WIDTH = 640;
const HEIGHT = 150;
const PAD = 16;

function percent(value: number): string {
  return `${(value * 100).toFixed(1)}%`;
}

/**
 * The word-error rate of recent runs as one line.
 *
 * A single run is not a trend: below two measured runs the component says so
 * instead of drawing a lone point that pretends to be one. Runs whose WER was
 * never measured (`null`) are left out — plotting them as zero would be a
 * quiet lie.
 */
export default function WerTrend({
  runs,
}: {
  runs: EvaluationComparisonRow[];
}) {
  const measured = runs
    .filter((run) => run.wer_micro !== null && run.wer_micro !== undefined)
    .sort(
      (a, b) => Date.parse(a.started_at ?? "") - Date.parse(b.started_at ?? ""),
    )
    .slice(-12);

  if (measured.length < 2) {
    return (
      <p className="hint" data-testid="wer-trend-note">
        At least two measured runs are needed to draw a trend
        {measured.length === 1
          ? " — one run is on record so far."
          : " — none are on record so far."}
      </p>
    );
  }

  const worst = Math.max(...measured.map((run) => run.wer_micro ?? 0));
  // A little headroom above the worst run, and a floor so a run of perfect
  // zeros still gets a sane axis instead of dividing by zero.
  const ceiling = Math.max(worst * 1.15, 0.02);
  const x = (index: number) =>
    PAD + (index * (WIDTH - 2 * PAD)) / (measured.length - 1);
  const y = (value: number) =>
    HEIGHT - PAD - (value / ceiling) * (HEIGHT - 2 * PAD);
  const points = measured
    .map((run, index) => `${x(index)},${y(run.wer_micro ?? 0)}`)
    .join(" ");

  return (
    <figure className="wer-trend">
      <svg
        role="img"
        aria-label={`WER trend over the last ${measured.length} measured runs`}
        viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
      >
        <polyline
          points={points}
          style={{ fill: "none", stroke: "var(--accent)", strokeWidth: 2 }}
        />
        {measured.map((run, index) => (
          <circle
            key={run.evaluation_id}
            cx={x(index)}
            cy={y(run.wer_micro ?? 0)}
            r={4}
            style={{ fill: "var(--accent)" }}
          >
            <title>{`${percent(run.wer_micro ?? 0)} — ${run.provider}${
              run.model ? ` · ${run.model}` : ""
            } · ${run.evaluation_id}`}</title>
          </circle>
        ))}
        <text x={PAD} y={PAD} style={{ fill: "var(--muted-foreground)" }}>
          {percent(ceiling)}
        </text>
        <text
          x={PAD}
          y={HEIGHT - 4}
          style={{ fill: "var(--muted-foreground)" }}
        >
          0%
        </text>
      </svg>
      <figcaption className="hint">
        oldest to newest · {measured.length} measured runs · lower is better
      </figcaption>
    </figure>
  );
}

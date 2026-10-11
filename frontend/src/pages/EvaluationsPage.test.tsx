import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { delay, http, HttpResponse } from "msw";

import { createApiClient } from "../api/client";
import { ApiProvider } from "../api/context";
import { server } from "../test/server";
import EvaluationsPage from "./EvaluationsPage";

const BASE = "http://127.0.0.1:8200";

const row = (overrides: Record<string, unknown> = {}) => ({
  evaluation_id: "eval-1",
  provider: "faster-whisper",
  model: "large-v3-turbo",
  dataset_id: "stt-eval-core",
  dataset_version: "1.0.0",
  clips_scored: 8,
  clips_total: 8,
  wer_micro: 0.1,
  wer_macro: 0.118,
  cer_micro: 0.095,
  rtf_mean: 0.072,
  failures: 0,
  segment_availability_mean: 1.0,
  word_availability_mean: 1.0,
  insufficient_evidence: false,
  started_at: "2026-10-11T20:00:00Z",
  ...overrides,
});

const record = (overrides: Record<string, unknown> = {}) => ({
  evaluation_id: "eval-1",
  dataset_id: "stt-eval-core",
  dataset_version: "1.0.0",
  provider: "faster-whisper",
  model: "large-v3-turbo",
  status: "COMPLETED",
  decoding: { language: "en" },
  normalization: { name: "stt-normalize-v1" },
  aggregate: { clips_scored: 8, clips_total: 8, wer_micro: 0.1, failures: 0 },
  by_group: {},
  insufficient_evidence: false,
  started_at: "2026-10-11T20:00:00Z",
  finished_at: "2026-10-11T20:00:12Z",
  duration_s: 12.0,
  mlflow: {
    status: "logged",
    run_id: "run-123",
    experiment: "stt-evaluation",
    tracking_uri: "http://127.0.0.1:5000",
  },
  artifact_key: "artifacts/evaluation/eval-1/result.json",
  clips: [
    {
      clip_id: "syn-en-tech-001",
      status: "scored",
      provider: "faster-whisper",
      model: "large-v3-turbo",
      audio_sha256: "abc",
      wer: 0.0,
      cer: 0.0,
      word_errors: { substitutions: 0, deletions: 0, insertions: 0 },
      char_errors: { substitutions: 0, deletions: 0, insertions: 0 },
      timings: { processing_s: 0.9, real_time_factor: 0.07 },
      timestamps: { word_availability: 1.0 },
      error: null,
    },
    {
      clip_id: "syn-en-numbers-001",
      status: "scored",
      provider: "faster-whisper",
      model: "large-v3-turbo",
      audio_sha256: "def",
      wer: 0.69,
      cer: 0.5,
      word_errors: { substitutions: 9, deletions: 0, insertions: 0 },
      char_errors: { substitutions: 9, deletions: 0, insertions: 0 },
      timings: { processing_s: 1.1, real_time_factor: 0.08 },
      timestamps: { word_availability: 1.0 },
      error: null,
    },
  ],
  ...overrides,
});

function renderPage() {
  return render(
    <ApiProvider client={createApiClient(BASE)}>
      <EvaluationsPage />
    </ApiProvider>,
  );
}

test("lists evaluations with a comparison table", async () => {
  server.use(
    http.get(`${BASE}/v1/evaluations`, () =>
      HttpResponse.json({
        evaluations: [
          row(),
          row({
            evaluation_id: "eval-2",
            provider: "sarvam",
            model: "saaras:v4",
            wer_micro: 0.21,
            word_availability_mean: null,
            insufficient_evidence: true,
            clips_scored: 1,
          }),
        ],
        count: 2,
      }),
    ),
    http.get(`${BASE}/v1/providers`, () =>
      HttpResponse.json({ providers: [] }),
    ),
  );

  renderPage();

  const table = await screen.findByRole("table", { name: /evaluations/i });
  const rows = within(table).getAllByRole("row");
  expect(rows).toHaveLength(3); // header + 2
  expect(within(table).getByText("faster-whisper")).toBeInTheDocument();
  expect(within(table).getByText("sarvam")).toBeInTheDocument();
  expect(within(table).getByText("10.0%")).toBeInTheDocument();
  expect(within(table).getByText("21.0%")).toBeInTheDocument();
  // Word timestamps missing is stated, not implied as zero.
  expect(within(table).getAllByText(/not reported/i).length).toBeGreaterThan(0);
  // One recording is not enough to rank a provider — say so.
  expect(within(table).getByText(/insufficient/i)).toBeInTheDocument();
});

test("runs an evaluation and shows the new result", async () => {
  let posted: unknown = null;
  let listCalls = 0;
  server.use(
    http.get(`${BASE}/v1/evaluations`, () => {
      listCalls += 1;
      return HttpResponse.json({
        evaluations: listCalls > 1 ? [row()] : [],
        count: listCalls > 1 ? 1 : 0,
      });
    }),
    http.get(`${BASE}/v1/providers`, () =>
      HttpResponse.json({ providers: [] }),
    ),
    http.post(`${BASE}/v1/evaluations`, async ({ request }) => {
      posted = await request.json();
      return HttpResponse.json(record(), { status: 201 });
    }),
  );

  renderPage();

  await userEvent.clear(screen.getByLabelText(/dataset/i));
  await userEvent.type(screen.getByLabelText(/dataset/i), "stt-eval-core");
  await userEvent.click(screen.getByRole("button", { name: /run evaluation/i }));

  const table = await screen.findByRole("table", { name: /evaluations/i });
  expect(within(table).getByText("eval-1")).toBeInTheDocument();
  expect(posted).toMatchObject({ dataset_id: "stt-eval-core" });
  expect(within(table).getByText("10.0%")).toBeInTheDocument();
});

test("shows the mlflow run and links to the tracking server", async () => {
  server.use(
    http.get(`${BASE}/v1/evaluations`, () =>
      HttpResponse.json({ evaluations: [], count: 0 }),
    ),
    http.get(`${BASE}/v1/providers`, () =>
      HttpResponse.json({ providers: [] }),
    ),
    http.get(`${BASE}/v1/evaluations/eval-1`, () =>
      HttpResponse.json(record()),
    ),
  );

  renderPage();

  await userEvent.type(screen.getByLabelText(/evaluation id/i), "eval-1");
  await userEvent.click(screen.getByRole("button", { name: /load/i }));

  const status = await screen.findByTestId("mlflow-status");
  expect(status).toHaveTextContent(/run-123/);
  const link = within(status).getByRole("link", { name: /mlflow/i });
  expect(link).toHaveAttribute("href", "http://127.0.0.1:5000");
});

test("a disabled or failed mlflow log is stated, never hidden", async () => {
  server.use(
    http.get(`${BASE}/v1/evaluations`, () =>
      HttpResponse.json({ evaluations: [], count: 0 }),
    ),
    http.get(`${BASE}/v1/providers`, () =>
      HttpResponse.json({ providers: [] }),
    ),
    http.get(`${BASE}/v1/evaluations/eval-2`, () =>
      HttpResponse.json(
        record({
          evaluation_id: "eval-2",
          mlflow: { status: "error", reason: "ConnectionError: refused" },
        }),
      ),
    ),
  );

  renderPage();

  await userEvent.type(screen.getByLabelText(/evaluation id/i), "eval-2");
  await userEvent.click(screen.getByRole("button", { name: /load/i }));

  const status = await screen.findByTestId("mlflow-status");
  expect(status).toHaveTextContent(/error/i);
  expect(status).toHaveTextContent(/refused/);
});

test("a dataset without generated audio shows the actionable error", async () => {
  server.use(
    http.get(`${BASE}/v1/evaluations`, () =>
      HttpResponse.json({ evaluations: [], count: 0 }),
    ),
    http.get(`${BASE}/v1/providers`, () =>
      HttpResponse.json({ providers: [] }),
    ),
    http.post(`${BASE}/v1/evaluations`, () =>
      HttpResponse.json(
        {
          error: {
            code: "DATASET_NOT_READY",
            message:
              "audio missing for 2 clip(s): a, b; run scripts/make_eval_corpus.py",
          },
        },
        { status: 409 },
      ),
    ),
  );

  renderPage();

  await userEvent.click(screen.getByRole("button", { name: /run evaluation/i }));

  const alert = await screen.findByRole("alert");
  expect(alert).toHaveTextContent(/DATASET_NOT_READY/);
  expect(alert).toHaveTextContent(/make_eval_corpus/);
});

test("per-clip scores are visible for a loaded evaluation", async () => {
  server.use(
    http.get(`${BASE}/v1/evaluations`, () =>
      HttpResponse.json({ evaluations: [], count: 0 }),
    ),
    http.get(`${BASE}/v1/providers`, () =>
      HttpResponse.json({ providers: [] }),
    ),
    http.get(`${BASE}/v1/evaluations/eval-1`, () =>
      HttpResponse.json(record()),
    ),
  );

  renderPage();

  await userEvent.type(screen.getByLabelText(/evaluation id/i), "eval-1");
  await userEvent.click(screen.getByRole("button", { name: /load/i }));

  const clips = await screen.findByRole("table", { name: /clips/i });
  expect(within(clips).getByText("syn-en-tech-001")).toBeInTheDocument();
  expect(within(clips).getByText("syn-en-numbers-001")).toBeInTheDocument();
  expect(within(clips).getByText("69.0%")).toBeInTheDocument();
  // The normalization behind the numbers is stated, not implied.
  expect(screen.getByText(/stt-normalize-v1/)).toBeInTheDocument();
});

test("draws error rates as meters as well as numbers", async () => {
  server.use(
    http.get(`${BASE}/v1/evaluations`, () =>
      HttpResponse.json({
        evaluations: [
          {
            evaluation_id: "eval-1",
            provider: "faster-whisper",
            model: "large-v3-turbo",
            dataset_id: "stt-eval-core",
            dataset_version: "1.0.0",
            clips_scored: 8,
            clips_total: 8,
            wer_micro: 0.1,
            wer_macro: 0.118,
            cer_micro: 0.095,
            rtf_mean: 0.072,
            failures: 0,
            segment_availability_mean: 1,
            word_availability_mean: 1,
            insufficient_evidence: false,
            started_at: "2026-10-11T00:00:00Z",
          },
        ],
        count: 1,
      }),
    ),
  );

  renderPage();
  const table = await screen.findByRole("table");

  const meters = within(table).getAllByRole("meter");
  expect(meters.length).toBe(3); // WER, CER, RTF
  expect(meters[0]).toHaveAttribute("aria-valuenow", "10");
  expect(within(table).getByText("10.0%")).toBeInTheDocument();
});

// ---------------------------------------------------------------------------
// Redesign: the overview has to answer "which provider is best, and can I
// trust that number" before anyone reads a table.
// ---------------------------------------------------------------------------

const manyRows = (count: number) =>
  Array.from({ length: count }, (_, index) =>
    row({
      evaluation_id: `eval-${index + 1}`,
      provider: index % 2 === 0 ? "faster-whisper" : "sarvam",
      model: index % 2 === 0 ? "large-v3-turbo" : "saaras:v4",
      wer_micro: 0.1 + index / 100,
      cer_micro: 0.09 + index / 100,
      rtf_mean: 0.07 + index / 1000,
      failures: index === 1 ? 2 : 0,
      started_at: `2026-10-1${(index % 9) + 1}T20:00:00Z`,
    }),
  );

function listHandler(rows: unknown[]) {
  return http.get(`${BASE}/v1/evaluations`, () =>
    HttpResponse.json({ evaluations: rows, count: rows.length }),
  );
}

test("summarises the runs before showing any table", async () => {
  server.use(
    listHandler([
      row({ evaluation_id: "eval-1", wer_micro: 0.1 }),
      row({ evaluation_id: "eval-2", provider: "sarvam", wer_micro: 0.21 }),
      row({ evaluation_id: "eval-3", provider: "deepgram", wer_micro: null, failures: 3 }),
    ]),
    http.get(`${BASE}/v1/providers`, () => HttpResponse.json({ providers: [] })),
  );

  renderPage();

  const summary = await screen.findByRole("group", { name: /evaluation summary/i });
  expect(within(summary).getByText(/^runs$/i).nextSibling).toHaveTextContent("3");
  expect(within(summary).getByText(/best wer/i).nextSibling).toHaveTextContent("10.0%");
  expect(within(summary).getByText(/^providers$/i).nextSibling).toHaveTextContent("3");
  expect(within(summary).getByText(/with failures/i).nextSibling).toHaveTextContent("1");
});

test("a run whose metrics could not be measured is not counted as a best", async () => {
  server.use(
    listHandler([row({ evaluation_id: "eval-1", wer_micro: null })]),
    http.get(`${BASE}/v1/providers`, () => HttpResponse.json({ providers: [] })),
  );

  renderPage();

  const summary = await screen.findByRole("group", { name: /evaluation summary/i });
  expect(within(summary).getByText(/best wer/i).nextSibling).toHaveTextContent(/not reported/i);
});

test("searches across provider, model and dataset", async () => {
  server.use(
    listHandler([
      row({ evaluation_id: "eval-1", provider: "faster-whisper" }),
      row({ evaluation_id: "eval-2", provider: "sarvam", model: "saaras:v4" }),
    ]),
    http.get(`${BASE}/v1/providers`, () => HttpResponse.json({ providers: [] })),
  );

  renderPage();
  const table = await screen.findByRole("table", { name: /evaluations/i });
  expect(within(table).getAllByRole("row")).toHaveLength(3);

  await userEvent.type(screen.getByRole("searchbox", { name: /search/i }), "saaras");

  expect(within(table).getAllByRole("row")).toHaveLength(2);
});

test("can hide everything that did not fail", async () => {
  server.use(
    listHandler([
      row({ evaluation_id: "eval-1", failures: 0 }),
      row({ evaluation_id: "eval-2", provider: "sarvam", failures: 2 }),
    ]),
    http.get(`${BASE}/v1/providers`, () => HttpResponse.json({ providers: [] })),
  );

  renderPage();
  const table = await screen.findByRole("table", { name: /evaluations/i });

  await userEvent.click(screen.getByRole("checkbox", { name: /with failures/i }));

  expect(within(table).getAllByRole("row")).toHaveLength(2);
  expect(within(table).getByText("sarvam")).toBeInTheDocument();
});

test("sorts by a column and tells assistive tech which way", async () => {
  server.use(
    listHandler([
      row({ evaluation_id: "eval-1", provider: "faster-whisper", wer_micro: 0.21 }),
      row({ evaluation_id: "eval-2", provider: "sarvam", wer_micro: 0.1 }),
    ]),
    http.get(`${BASE}/v1/providers`, () => HttpResponse.json({ providers: [] })),
  );

  renderPage();
  const table = await screen.findByRole("table", { name: /evaluations/i });

  const werHeader = within(table).getByRole("columnheader", { name: /wer/i });
  await userEvent.click(within(werHeader).getByRole("button"));

  expect(werHeader).toHaveAttribute("aria-sort", "ascending");
  const bodyRows = within(table).getAllByRole("row").slice(1);
  expect(within(bodyRows[0]).getByText("sarvam")).toBeInTheDocument();
});

test("shows a page of runs and offers the rest", async () => {
  server.use(
    listHandler(manyRows(11)),
    http.get(`${BASE}/v1/providers`, () => HttpResponse.json({ providers: [] })),
  );

  renderPage();
  const table = await screen.findByRole("table", { name: /evaluations/i });
  expect(within(table).getAllByRole("row")).toHaveLength(9); // header + 8

  await userEvent.click(screen.getByRole("button", { name: /show 3 more/i }));

  expect(within(table).getAllByRole("row")).toHaveLength(12);
});

test("an empty history explains how to make one", async () => {
  server.use(
    listHandler([]),
    http.get(`${BASE}/v1/providers`, () => HttpResponse.json({ providers: [] })),
  );

  renderPage();

  const empty = await screen.findByTestId("evaluations-empty");
  expect(empty).toHaveTextContent(/no evaluations yet/i);
  expect(empty).toHaveTextContent(/reference corpus/i);

  await userEvent.click(within(empty).getByRole("button", { name: /run an evaluation/i }));
  expect(screen.getByLabelText(/dataset/i)).toHaveFocus();
});

test("a failed list load is reported and can be retried", async () => {
  let calls = 0;
  server.use(
    http.get(`${BASE}/v1/evaluations`, () => {
      calls += 1;
      return calls === 1
        ? HttpResponse.json(
            { error: { code: "INTERNAL_ERROR", message: "database is down" } },
            { status: 500 },
          )
        : HttpResponse.json({ evaluations: [row()], count: 1 });
    }),
    http.get(`${BASE}/v1/providers`, () => HttpResponse.json({ providers: [] })),
  );

  renderPage();

  const alert = await screen.findByRole("alert");
  expect(alert).toHaveTextContent(/database is down/i);

  await userEvent.click(within(alert).getByRole("button", { name: /retry/i }));

  const table = await screen.findByRole("table", { name: /evaluations/i });
  expect(within(table).getByText("faster-whisper")).toBeInTheDocument();
});

test("a second run cannot be started while one is in flight", async () => {
  let posts = 0;
  server.use(
    listHandler([]),
    http.get(`${BASE}/v1/providers`, () => HttpResponse.json({ providers: [] })),
    http.post(`${BASE}/v1/evaluations`, async () => {
      posts += 1;
      await delay(150);
      return HttpResponse.json(record(), { status: 201 });
    }),
  );

  renderPage();
  const runButton = await screen.findByRole("button", { name: /run evaluation/i });

  await userEvent.click(runButton);
  await userEvent.click(runButton);

  expect(posts).toBe(1);
});

test("a run in flight says it is running and how long it has been", async () => {
  server.use(
    listHandler([]),
    http.get(`${BASE}/v1/providers`, () => HttpResponse.json({ providers: [] })),
    http.post(`${BASE}/v1/evaluations`, async () => {
      await delay(150);
      return HttpResponse.json(record(), { status: 201 });
    }),
  );

  renderPage();
  await userEvent.click(await screen.findByRole("button", { name: /run evaluation/i }));

  const progress = screen.getByRole("progressbar", { name: /evaluation in progress/i });
  expect(progress).toHaveTextContent(/running for \d+s/i);
  expect(screen.getByRole("button", { name: /running/i })).toBeDisabled();
});

test("a run's detail opens from the table and closes again", async () => {
  server.use(
    listHandler([row({ evaluation_id: "eval-1" })]),
    http.get(`${BASE}/v1/providers`, () => HttpResponse.json({ providers: [] })),
    http.get(`${BASE}/v1/evaluations/eval-1`, () => HttpResponse.json(record())),
  );

  renderPage();
  const table = await screen.findByRole("table", { name: /evaluations/i });

  await userEvent.click(within(table).getByRole("button", { name: "eval-1" }));

  const detail = await screen.findByTestId("evaluation-detail");
  expect(within(detail).getByText(/run-123/)).toBeInTheDocument();

  await userEvent.click(within(detail).getByRole("button", { name: /close/i }));
  expect(screen.queryByTestId("evaluation-detail")).not.toBeInTheDocument();
});

test("per-group results are shown when the run has them", async () => {
  server.use(
    listHandler([row({ evaluation_id: "eval-1" })]),
    http.get(`${BASE}/v1/providers`, () => HttpResponse.json({ providers: [] })),
    http.get(`${BASE}/v1/evaluations/eval-1`, () =>
      HttpResponse.json(
        record({
          by_group: {
            en: { "faster-whisper": { clips_scored: 6, wer_micro: 0.08 } },
            hi: { "faster-whisper": { clips_scored: 2, wer_micro: 0.24 } },
          },
        }),
      ),
    ),
  );

  renderPage();
  const table = await screen.findByRole("table", { name: /evaluations/i });
  await userEvent.click(within(table).getByRole("button", { name: "eval-1" }));

  const groups = await screen.findByRole("table", { name: /by group/i });
  expect(within(groups).getByText("en")).toBeInTheDocument();
  expect(within(groups).getByText("hi")).toBeInTheDocument();
  expect(within(groups).getByText("8.0%")).toBeInTheDocument();
});

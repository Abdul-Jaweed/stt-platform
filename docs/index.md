# STT Platform

STT Platform is a **provider-neutral speech-to-text platform**. One headless
`/v1` API turns audio into timestamped transcripts through swappable
providers — a local Faster-Whisper `large-v3-turbo` on CUDA, or a managed API
(Deepgram, ElevenLabs Scribe, Sarvam) — behind a single internal contract. A
separate React + TypeScript console consumes only that API, so voice agents,
scripts and the UI are all just clients.

- **Durability by default** — job state in PostgreSQL, dispatch through a
  transactional outbox, execution as Temporal workflows; a killed worker does
  not lose a job.
- **Honesty by design** — a metric that could not be measured is `None`,
  never `0.0`; a failed job says whether retrying it is honest.
- **Privacy by structure** — local-only audio is never silently routed to a
  cloud provider; transcripts, audio bytes and secrets never reach the logs.

## Start it

The whole platform — database, object storage, workflow engine, API, worker
and console — runs from one compose file, started by one script:

```bash
./launch.sh          # start (builds on first run, generates .env if missing)
./launch.sh gpu      # same, with CUDA for the local model
./launch.sh status   # what is up, what is healthy, what it answered
```

Docker Desktop (or Docker Engine with the compose v2 plugin) is the only
prerequisite. When it answers:

| | |
| --- | --- |
| console | <http://127.0.0.1:8080> |
| API and OpenAPI docs | <http://127.0.0.1:8200/v1> · <http://127.0.0.1:8200/docs> |

The console credentials are generated into a gitignored `.env` on first
launch and printed once. The full stack guide — ports, environment variables,
GPU passthrough, backup, troubleshooting — is
[Deployment and configuration](deployment.md).

## The docs

| Page | What is in it |
| --- | --- |
| [API](api.md) | the `/v1` surface, endpoint by endpoint, with example runs and the committed OpenAPI document |
| [Deployment and configuration](deployment.md) | the one-command Docker stack: ports, environment variables, GPU, backup, troubleshooting |
| [Operations](operations.md) | running it: routine tasks, backups, capacity |
| [Reliability](reliability.md) | the guarantees (outbox, retries, cancellation) and the known gaps |
| [Incident recovery](incident-recovery.md) | runbooks: what breaks, what the platform does, what you do |
| [Observability](observability.md) | traces, logs, the metric catalog, dashboards, alerts |
| [Evaluation](evaluation.md) | datasets, WER/CER and timestamp metrics, MLflow, limits |
| [Streaming](streaming.md) | the WebSocket endpoint that transcribes while you speak — local model only, nothing persisted |
| [UI](ui.md) | the React console: pages, the browser recorder, design rules |
| [Providers](providers.md) | the capability matrix, provider configuration, fidelity rules |
| [Live provider verification](live-provider-verification.md) | live vendor runs: numbers, defects, evidence |
| [Architecture](architecture.md) | system boundaries, data model, ADR log, ports — plus a [standalone diagrams section](architecture/index.html) with two interactive diagrams: [platform architecture](architecture/stt-platform-architecture.html) · [job lifecycle](architecture/transcription-job-lifecycle.html) |

The phase-by-phase build history (evidence and limitations per phase) is in
the sidebar under **Phase reports**.

## Verified on this stack

Not a promise — the checks that were actually run, on Windows 11 with Docker
Desktop and an RTX 4060 (the full record is
[Deployment §9](deployment.md#9-what-was-verified-on-this-stack)):

- `./launch.sh` (CPU default) — both images built, 8 containers up,
  `/v1/health/ready` → `{"status":"ready"}`
- console through nginx on `:8080` → 200; sign-in via `POST /v1/auth/login`
  → 200, wrong password → 401
- synchronous transcription (CPU `small`) — job SUCCEEDED, 1 segment,
  24 word timings, `en` detected
- async path (`STT_MAX_SYNC_DURATION_S=0`) — 201 `QUEUED` → `PROCESSING` →
  `SUCCEEDED`, run by the worker container
- evaluation inside the stack — 8/8 clips scored, 0 failures, WER 0.117
  (CPU `small`)
- GPU passthrough (`./launch.sh gpu`) — `large-v3-turbo` on CUDA, RTF 0.62
  cold, 0.099 warm
- browser walkthrough — landing page, theme toggle, sign-in, evaluations
  summary with live data

## Docs site

This site is built from `docs/` with MkDocs Material and deployed to GitHub
Pages by `.github/workflows/docs.yml`. The build is strict — warnings are
errors, so a page missing from the navigation or a broken link fails the run
instead of shipping quietly. The internal plan files under `docs/superpowers/`
are excluded from the site.

**Preview locally** (needs `uv` on the host; `uvx` fetches the toolchain on
the fly, the same way CI does — no project dependency is added):

```bash
uvx --with mkdocs-material mkdocs serve           # live preview at http://127.0.0.1:8000
uvx --with mkdocs-material mkdocs build --strict  # one-shot build into site/
```

**Deployment.** GitHub Pages is enabled for this repository with the build
type `workflow`, and the site will live at
<https://abdul-jaweed.github.io/stt-platform/>. Every push to `main` that
touches `docs/**`, `mkdocs.yml` or the workflow itself rebuilds and deploys
the site; the first deployment lands with the next such push (the workflow
can also be run manually). If Pages is ever disabled
(a fork, a settings reset), a maintainer re-enables it with:

```bash
gh api -X POST repos/Abdul-Jaweed/stt-platform/pages -f build_type=workflow
```

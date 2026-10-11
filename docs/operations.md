# Operations

Everything an operator needs: what runs, on which ports, how to start and
stop it, what to watch, and which knobs change behaviour. Written for the
single-node deployment this project actually is — where a second node would
need a shared limiter and a shared model cache is called out explicitly.

## Services and ports

| Service | Where | Port | Notes |
|---|---|---|---|
| API (FastAPI/uvicorn) | host, `backend/` | 8078 (dev), 8000 (default) | serves `/v1`, `/metrics` when enabled |
| Worker (Temporal) | host, `backend/` | — | long audio, retries, managed providers |
| Postgres 16 | compose, `infrastructure/` | 25432 | jobs, transitions, outbox, evaluations |
| RustFS (S3) | compose, `infrastructure/` | 29000 | `stt-audio`, `stt-artifacts` |
| Temporal | compose, `infrastructure/` | 7233 (gRPC), 28080 (UI) | workflows, retries, timers |
| MLflow | `I:\voice-agent-course\mlflow` | 5000 | evaluation tracking (optional) |
| Prometheus (opt-in) | either process | 9466 API / 9467 worker | distinct ports: Windows allows a silent clash |

Bring the stack up:

```bash
cd infrastructure && docker compose up -d
docker compose ps                     # 4 stt-* containers, 3 healthy + 1 exited(0)
curl -s http://127.0.0.1:8078/v1/health/ready
```

Start the API and the worker (two shells, from `backend/`):

```bash
uv run python -m uvicorn stt_platform.main:app --host 127.0.0.1 --port 8078
uv run python -m stt_platform.workers.transcription_worker
```

## Health and readiness

| Endpoint | Means |
|---|---|
| `GET /v1/health/live` | the process is up (never touches dependencies) |
| `GET /v1/health/ready` | database reachable; storage reachable when configured |
| `GET /metrics` | Prometheus exposition, only when `STT_PROMETHEUS_ENABLED=true` |

Health endpoints are deliberately **unauthenticated**: a probe has no API key.
Everything else under `/v1` requires one when `STT_AUTH_ENABLED=true`.

## Configuration that changes behaviour

| Setting | Default | Effect |
|---|---|---|
| `STT_AUTH_ENABLED` | `false` | when true, every `/v1` call needs `X-API-Key`; the key's tenant — never the request — sets the scope |
| `STT_API_KEYS` | empty | `tenant:key,tenant:key`; empty with auth on fails closed (401 everywhere) |
| `STT_MAX_REQUESTS_PER_MINUTE` | `0` (off) | per-tenant quota for the transcriptions API; **per API process** — a fleet needs the shared Redis the spec reserves |
| `STT_RETENTION_DAYS` | `0` (keep) | window used by `scripts/purge_expired.py` |
| `STT_MAX_SYNC_DURATION_S` | 180 | audio at or below this is transcribed inside the request; above it goes to the worker |
| `STT_GPU_CONCURRENCY` / `STT_MANAGED_CONCURRENCY` | 1 / 4 | in-flight GPU inference / managed provider calls |
| `STT_OTEL_ENABLED`, `STT_PROMETHEUS_ENABLED`, `STT_LOG_JSON` | false | observability is off by default; turning it on must never break a transcription |

Secrets live only in `backend/.env` (gitignored). `backend/.env.example`
documents every variable and carries no values.

## Routine tasks

**Delete one job** (objects, artifacts, rows — authorized by scope):

```bash
curl -X DELETE http://127.0.0.1:8078/v1/transcriptions/<job_id> -H "X-API-Key: ..."
```

**Apply retention** — always dry-run first:

```bash
python scripts/purge_expired.py --dry-run --older-than-days 30
python scripts/purge_expired.py --older-than-days 30
```

Only terminal jobs (`SUCCEEDED`/`FAILED`/`CANCELLED`) are candidates; work in
flight is never deleted, whatever its age.

**Load test** (real transcriptions, real GPU):

```bash
python scripts/load_test.py --requests 12 --concurrency 4 --json-out results/phase7_load/result.json
```

**Re-run an evaluation** (needs MLflow for tracking, optional):

```bash
curl -X POST http://127.0.0.1:8078/v1/evaluations -H 'Content-Type: application/json' \
  -d '{"dataset_id": "stt-eval-core", "provider": "faster-whisper"}'
```

## Capacity, measured

`results/phase7_load/result.json` and `result_24x8.json` are real runs against
the real `large-v3-turbo` on the RTX 4060 (10 s clips):

| Load | Wall | Throughput | p50 | p95 | Errors |
|---|---|---|---|---|---|
| 12 requests, concurrency 4 | 8.5 s | 1.41 req/s | 2.63 s | 2.66 s | 0 |
| 24 requests, concurrency 8 | 16.5 s | 1.45 req/s | 5.25 s | 5.45 s | 0 |

Read it honestly: throughput **saturates** near 1.45 req/s because one GPU
serializes inference (`STT_GPU_CONCURRENCY=1`); extra concurrency buys queue
depth, not speed. Latency therefore grows roughly linearly with queue depth.
To raise the ceiling: more GPUs (raise `STT_GPU_CONCURRENCY` per device), or
route eligible tenants to a managed provider (their own concurrency budget).

## Backups

| What | How | Why it matters |
|---|---|---|
| Postgres | `docker exec stt-postgres pg_dump -U stt stt > backup.sql` | jobs, transitions, outbox, evaluations — the state machine's truth |
| RustFS data | the `rustfs-data` volume, or `mc mirror`/`aws s3 sync` to another bucket | originals + transcript artifacts |
| `backend/.env` | password manager / secret store | provider keys; **never** in git |
| `models/huggingface` | re-downloadable; warm cache only | cold start cost (~4.7 s load) |

A restore is: compose up → `psql < backup.sql` → restore objects → start API
and worker. In-flight jobs resume: the outbox re-dispatches anything that was
committed but never started, and Temporal replays the rest.

## Scaling notes

* One API process and one worker are the supported shape. A second worker is
  safe (Temporal coordinates); a second *API* process needs the shared
  limiter and a shared model cache before the quotas mean anything.
* Keep `STT_MAX_SYNC_DURATION_S` low if clients should get fast 202s rather
  than long-held requests; the worker is the right place for long audio.
* Managed providers are key-gated: a provider registers only when its API key
  is configured, so a missing key degrades to "not offered", never to "500".

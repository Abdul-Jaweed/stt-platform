# Incident recovery

What each failure looks like from the outside, what the platform does on its
own, and what the operator does next. Every procedure here is one this
project has actually survived (a host power cut, a killed worker, a dead
MLflow, a provider outage) or one exercised by a test in `backend/tests/`.

## Ground rules

* **The database is the truth.** Job state lives in Postgres, never in a
  process. If a process dies, nothing is lost; if Postgres dies, everything
  waits.
* **The outbox is at-least-once.** A row committed before dispatch survives a
  crash and is retried on the next tick; a job may therefore be *started*
  twice, and every activity is written to be safe under that.
* **Failures are recorded, never hidden.** A job that could not run is
  `FAILED` with a code (`STORAGE_ERROR`, `PROVIDER_ERROR`, …) and a
  `retryable` flag; a metric that could not be measured is `None`, never 0.
* **Never fake a recovery.** If a dependency is still down, say so; the job
  stays `FAILED` and retryable.

## Host restart / power cut

Symptom: everything is gone; `docker ps` is empty.

```bash
cmd //c start "" "C:\Program Files\Docker\Docker\Docker Desktop.exe"
# wait for: docker info   (Docker Desktop takes ~30-60 s on this host)
cd infrastructure && docker compose up -d && docker compose ps
curl -s http://127.0.0.1:8078/v1/health/ready     # after starting the API
```

Then verify the three things that actually matter:

1. **4 `stt-*` containers** (postgres, rustfs, temporal, temporal-ui);
   `stt-storage-init` exits 0 by design.
2. **`127.0.0.1:7233` reachable** — if Temporal is down, integration tests
   *skip* rather than fail, which is easy to misread as "green".
3. **A job that was `QUEUED` when the host died** moves to `PROCESSING`
   within a minute of the worker starting (the outbox re-dispatches it).

Data is on Docker volumes; nothing needs restoring after a clean power cut.
A `docker compose down -v` is the destructive variant — that drops the
volumes, and only the backups in `docs/operations.md` bring the data back.

## Postgres is down

Symptom: `/v1/health/ready` fails; every write returns 503; the worker logs
connection errors and Temporal retries the activities.

```bash
docker compose ps stt-postgres
docker logs --tail 50 stt-postgres
docker compose up -d stt-postgres
```

Nothing is lost: jobs sit at their last committed transition. When Postgres
returns, the worker's next activity attempt succeeds and the job continues.
If the volume is corrupt, restore from `pg_dump` (operations.md) — jobs
created after the dump are gone; say so rather than pretending.

## Object storage (RustFS) is down

Symptom: uploads fail. A **sync** job fails immediately with
`STORAGE_ERROR` and `retryable: true` (the local copy survived, so a retry is
genuinely possible). An **async** job with no local copy cannot be retried at
all — its failure says `retryable: false`.

```bash
docker compose ps stt-rustfs && docker compose up -d stt-rustfs
curl -X POST .../v1/transcriptions/<id>/retry    # after storage is back
```

A retry of a job whose original never reached storage **re-uploads it first**
and only then proceeds; if storage is still down the job stays `FAILED` and
retryable, with a fresh log line saying why.

## Temporal is down

Symptom: `/v1/transcriptions` still returns 202 (the row is committed) but
the job stays `QUEUED`; the outbox shows rows with `attempt_count > 0` and a
`last_error`. Sync jobs (short audio) keep working — they never touch Temporal.

```sql
SELECT id, job_id, attempt_count, last_error FROM outbox WHERE dispatched_at IS NULL;
```

```bash
docker compose up -d stt-temporal
```

The dispatcher retries every tick; pending rows start as soon as the server
answers. No manual re-enqueue is needed — that is the point of the outbox.

## Provider outage (managed API or local model)

Symptom: jobs go `RETRY_WAIT` then either `SUCCEEDED` or `FAILED` with
`PROVIDER_ERROR`. The workflow retries with bounded backoff and jitter, and
every attempt is a row in `provider_attempts`.

* Transient (5xx, timeout, rate limit): retried automatically; watch
  `stt_provider_attempts_total{outcome="retry"}`.
* Permanent (4xx auth, unsupported language): `FAILED`, `retryable: false` —
  fix the key or the request, do not retry.
* Local model missing/OOM: the adapter fails fast; free VRAM and retry.

## Stuck jobs

| Looks like | Cause | Do |
|---|---|---|
| `QUEUED` for minutes, outbox pending | Temporal unreachable | see above |
| `PROCESSING` forever, workflow alive | a wedged provider call | let the workflow's timeout fire; `POST /cancel` if the tenant wants out |
| `PROCESSING`, no workflow (Temporal UI empty) | workflow history lost | `POST /transcriptions/<id>/retry` (the local copy or the stored original is enough) |
| Illegal transition in the log | a cancelled workflow writing after the fact | expected and harmless: it is a logged no-op, the DB stays authoritative |

## Disk full

Symptom: uploads fail with `STORAGE_ERROR`; Postgres may refuse writes.

```bash
docker system df && du -sh I:/voice-agent-course/local-stt/models/*
python scripts/purge_expired.py --dry-run --older-than-days 30   # then without --dry-run
docker system prune -f        # dangling images/build cache only
```

The model cache is re-downloadable; `results/` and the eval corpus are
regenerable (`scripts/make_eval_corpus.py`).

## Credential exposure

1. Rotate the provider key at the vendor **first**.
2. Update `backend/.env` (never git), restart the API and worker.
3. Verify the old key is gone from logs: `grep -R "<old-key-prefix>" logs/` —
   should be empty; the redactor masks key-shaped strings, but check anyway.
4. If it ever reached git, rotate and rewrite history before pushing; the
   repository's own tests (`test_secret_hygiene.py`) are the tripwire.

## Data deletion request (DPR)

```bash
# one job, tenant-scoped
curl -X DELETE http://127.0.0.1:8078/v1/transcriptions/<job_id> -H "X-API-Key: ..."
```

Deletion removes the original audio, the transcript artifact, any retained
raw provider response, the local working copy, and the job's rows
(transitions, attempts, outbox — via the database cascade). Deleting a job
that is still running cancels it first. Verify with a `GET` (404) and by
listing the tenant's jobs. For a whole tenant: purge every job, then remove
its key from `STT_API_KEYS` and restart the API.

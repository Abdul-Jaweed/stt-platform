# Phase 7 report — Hardening

Spec: `IDEA.md` §"Phase 7 — Hardening": *"Run end-to-end, load,
failure-recovery, security, tenant-isolation, retention, and deletion tests.
Document operations and incident recovery."*

Status: **complete**. Every claim below comes from a run recorded in this
report; nothing is extrapolated.

## 1. What changed

| Area | Files |
|---|---|
| Authentication | `api/security.py` (new): key parsing, constant-time resolution, `get_scope` |
| Quotas | `api/ratelimit.py` (new): sliding window per tenant; wired in `main.py`, `api/v1/transcriptions.py` |
| Errors | `domain/errors.py` (+`Unauthorized`, `RateLimited`), `api/errors.py` (401/429 mapping) |
| Settings | `core/config.py`: `auth_enabled`, `api_keys`, `max_requests_per_minute`, `retention_days`, `activity_attempt_timeout_s` |
| Deletion | `services/retention.py` (new), `JobService.delete`, `JobRepository.delete`, `DELETE /v1/transcriptions/{id}` |
| Retention tool | `scripts/purge_expired.py` (new) |
| Load harness | `scripts/load_test.py` (new) |
| Recovery bound | `workflows/transcription.py` + `services/temporal_starter.py`: per-attempt timeout from settings (was a hard-coded 4 h) |
| Retry honesty | `services/jobs.py`: a retry of a job whose original never reached storage re-uploads it first |
| Ops docs | `docs/operations.md`, `docs/incident-recovery.md` (new) |
| e2e | `scripts/e2e_phase7.py` (new): 28 checks incl. a live worker kill |

Tests added: `tests/unit/test_api_keys.py`, `test_rate_limit.py`,
`test_secret_hygiene.py`; `tests/integration/test_auth_tenancy.py`,
`test_retention_deletion.py`, `test_failure_recovery.py`,
`test_security_buckets.py` (+2 log-hygiene tests inside the auth suite).

## 2. Authentication and tenant isolation

The tenant is **server-assigned**: it comes from the presented `X-API-Key`,
never from a header or body field (`api/security.py`). Keys are compared with
`hmac.compare_digest`, and with auth enabled an empty key list fails *closed*
(401 everywhere) rather than open. Health endpoints stay unauthenticated so
probes work.

Verified through the real API (`test_auth_tenancy.py`, 22 tests; e2e §2–3):
missing key → 401; unknown key → 401; tenant A's job read by tenant B → **404
`JOB_NOT_FOUND`** (existence is not confirmed to another tenant); B deleting
A's job → 404; A deleting its own → 204 then 404; presigned upload keys are
bound to the uploading tenant, and presenting another tenant's key is refused
with `INVALID_OBJECT_KEY`.

**Retrieval is authorized as strictly as creation** — every read, retry,
cancel and delete path goes through the same scope resolution.

## 3. Quotas

`STT_MAX_REQUESTS_PER_MINUTE` (0 = off) applies a sliding window per tenant at
the API edge. Verified at the edge: two calls pass, the third returns **429
`RATE_LIMITED`**, and a second tenant is unaffected.

Honest limitation, documented in `docs/operations.md`: the limiter lives **in
the API process**. That is correct for the single-node deployment this project
is; a fleet needs the shared Redis the spec reserves for ephemeral
coordination. It is off by default, so the developer flow and the e2e drivers
are unaffected.

## 4. Deletion and retention

`DELETE /v1/transcriptions/{id}` removes the original audio, the transcript
artifact, any retained raw provider response, the local working copy, and the
job's rows — transitions, provider attempts and outbox rows go with the job
via the database cascade. A job that is still running is cancelled first.

Verified (`test_retention_deletion.py`, 7 tests; e2e §5): objects are really
gone from RustFS, rows really gone from Postgres, and another tenant's delete
attempt is a 404 that changes nothing.

`scripts/purge_expired.py` applies a retention window; `--dry-run` is
non-destructive and refuses to guess when retention is disabled. Selection is
deliberately conservative — **only terminal jobs** are candidates, so a purge
can never delete work in flight (unit-tested directly). e2e §6: 86 candidates
found, 87 job rows before and after the dry run.

## 5. Load

`scripts/load_test.py` drives the real API, real GPU, real model — no mocks.
Raw evidence: `results/phase7_load/result.json`, `result_24x8.json`.

| Load | Wall | Throughput | p50 | p95 | p99 | Errors |
|---|---|---|---|---|---|---|
| 12 requests, concurrency 4 | 8.52 s | 1.41 req/s | 2.63 s | 2.66 s | 3.31 s | 0 |
| 24 requests, concurrency 8 | 16.51 s | 1.45 req/s | 5.25 s | 5.45 s | 6.09 s | 0 |

The honest reading: throughput **saturates near 1.45 req/s** because a single
GPU serializes inference (`STT_GPU_CONCURRENCY=1`); added concurrency buys
queue depth, not speed, so latency grows roughly linearly while throughput
stays flat. Zero errors in both runs. Raising the ceiling means more GPUs or
routing eligible tenants to a managed provider.

## 6. Failure recovery

Unit/integration (`test_failure_recovery.py`, 5 tests):

* a storage outage fails the job with `STORAGE_ERROR` and a truthful
  `retryable` flag, and the retry succeeds once storage is back;
* a fresh API process sees the same durable state (state lives in Postgres);
* an outbox row survives a dispatcher crash mid-flight and is dispatched
  later — at-least-once, not at-most-once;
* a healed provider lets a retry succeed, and the previous failure stays
  visible as history while the status is authoritative.

End-to-end chaos (`scripts/e2e_phase7.py` §4) — the headline evidence:

1. a 30 s clip is submitted and reaches `PROCESSING`;
2. the worker process tree is killed, and the kill is **verified** (no
   `transcription_worker` process survives — the first version of this check
   matched its own PowerShell command line and lied, see §7);
3. the job stays `PROCESSING` for the whole attempt timeout with no worker —
   nothing advances, nothing is lost;
4. the worker returns and the job finishes **on its own**, producing a real
   3-segment transcript;
5. the transition trail records the recovery end to end:

```
VALIDATING -> STORED -> QUEUED -> PROCESSING -> RETRY_WAIT -> QUEUED -> PROCESSING -> SUCCEEDED
```

That recovery is bounded by `STT_ACTIVITY_ATTEMPT_TIMEOUT_S` (default 1800 s,
set to 15 s for the e2e). Before this phase the attempt timeout was a
hard-coded 4 hours, so a killed worker's job could stall for hours; now it is
a documented knob that trades long audio against recovery time.

## 7. Security pass

* **Buckets are private** — asserted against the real RustFS: anonymous HTTP
  reads are refused (403), bucket ACLs carry no `AllUsers`/`AuthenticatedUsers`
  grant, and no bucket policy names a wildcard principal
  (`test_security_buckets.py`, 3 tests).
* **Secret hygiene as tests, not habits** (`test_secret_hygiene.py`, 5 tests):
  no credential-shaped strings in tracked files (AWS/OpenAI/Google/HF/private
  key patterns), no `.env` in the index, no audio or model binaries (ADR-007),
  no values for secret keys in `.env.example`, and no non-`VITE_` variables in
  the frontend's environment.
  These tests earned their keep immediately: they caught the owner's own
  recording (`my_recording/my_recordings.m4a`) swept into a commit. It is now
  untracked, gitignored, and gone from history — the file stays on the owner's
  disk and is never published.
* **Log hygiene** (`test_auth_tenancy.py`): during an authenticated upload and
  a result fetch, neither the API key nor the transcript text appears in the
  captured logs — the access path does not leak what the redactor protects.
* **Media limits**: `ffprobe` already runs under a timeout
  (`audio/inspect.py`), upload size and duration are bounded, and extensions
  are never trusted — validation is content-based.

## 8. Operations and incident recovery

`docs/operations.md`: services and ports, start/stop, health semantics,
configuration that changes behaviour, routine tasks (delete, purge, load
test, re-run an evaluation), measured capacity, backups, scaling notes.

`docs/incident-recovery.md`: runbooks for a host restart/power cut, Postgres
down, object storage down, Temporal down, provider outage, stuck jobs, disk
full, credential exposure and a data-deletion request — each written as
symptom → what the platform does on its own → what the operator does.

## 9. Truly executed verification

| Command | Result |
|---|---|
| `uv run pytest -q` (backend) | see §10 — full suite incl. the 56 new Phase-7 tests |
| `uv run pytest -m gpu -q` | 3 passed (real GPU evaluation) |
| `uv run ruff check src tests ../scripts/*.py` | clean |
| `npm test` (frontend) | 48 passed (12 files) |
| `npm run build` | green |
| `python scripts/load_test.py` ×2 | 0 errors, numbers in §5 |
| `python scripts/e2e_phase7.py` | **28/28 checks passed**, `results/phase7_e2e/result.json` |
| `python scripts/purge_expired.py --dry-run` | refused when disabled; 86 candidates with an explicit window; nothing deleted |

## 10. Limitations (stated, not hidden)

* The rate limiter is per API process (see §3).
* `STT_AUTH_ENABLED` defaults to **false** so the local flow works; a
  deployment must turn it on and configure keys, otherwise every caller is
  tenant `local`. This is documented in operations.md rather than silently
  enforced, because the developer flow (and the e2e drivers) depend on it.
* Encryption in transit is TLS-at-the-proxy, not implemented in-process; the
  compose stack is plain HTTP on localhost by design.
* The chaos test kills a worker; it does not (yet) kill Postgres or RustFS
  mid-run. Their failure modes are covered by tests at the service level and
  by runbooks, not by a live kill.
* The 30 s e2e clip is synthesized by ffmpeg (ADR-007: no audio in the repo).

## 11. Next

Phase 7 is the last phase in the spec. Remaining work is owner-facing: publish
the repository (public GitHub), and — if the owner confirms the reference text
— score the owner's own recording (`my_recording/my_recordings.m4a`) as a
`human` clip in the evaluation dataset.

# Phase 7 — Hardening

Status: **in progress** (T1–T8)

Spec: `IDEA.md` §"Phase 7 — Hardening" — *"Run end-to-end, load,
failure-recovery, security, tenant-isolation, retention, and deletion tests.
Document operations and incident recovery."* Plus the standing requirements:
§"Security and privacy" (authentication, authorization, tenant isolation,
quotas, credentials out of Git and the frontend, log redaction, explicit
retention and deletion, never silently route local-only audio to cloud, keep
development shortcuts separate from production security) and §"Storage and
data lifecycle" (opaque keys, access controls, buckets never public,
retention policies, deletion workflows).

Phases 0–6 are complete. This phase adds **no new product surface** except
where the spec demands it (deletion, authentication); it is about proving the
existing surface survives contact with the real world, and writing down how
to run and recover it.

## Design decisions

1. **Authentication is a switch, not a rewrite.** `STT_AUTH_ENABLED=false`
   (default) keeps the local single-tenant developer flow working; with it
   on, every `/v1` request must carry `X-API-Key`, and the key's configured
   tenant becomes the request scope. Development shortcuts stay separate
   from production security (spec, verbatim).
2. **The tenant is never taken from the client.** Scope comes from the
   authenticated key only; a request can never name its own tenant. Reads,
   writes, retries, cancels and deletions are all filtered by that scope.
3. **Cross-tenant access answers `404`, not `403`.** A 403 confirms that the
   object exists; the platform must not be an existence oracle for another
   tenant.
4. **Deletion is explicit and complete**: object storage (original + result
   artifacts) first, then the row (FK cascades transitions, attempts, outbox).
   A purge job applies the same path to everything older than the retention
   window — and never touches non-terminal jobs.
5. **Retention defaults to "keep forever" (`STT_RETENTION_DAYS=0`).**
   Deleting a user's data is a decision, not a default; the mechanism ships,
   the policy is configuration, and the purge script supports `--dry-run`.
6. **Load numbers are recorded, not asserted.** The harness measures p50/p95
   and throughput on this machine and writes them to `results/`; no test
   claims a latency the hardware cannot guarantee.
7. **Failure recovery is proven by killing things.** A chaos driver kills the
   worker mid-job and restarts it, stops and restarts the API while a client
   polls, and verifies the job still finishes with a truthful transition
   trail. What cannot be automated (power loss, disk failure) is documented
   from real incidents already survived (two power cuts during Phases 2–5).
8. **Security tests are adversarial**: anonymous bucket access must fail,
   logs must not contain transcripts or keys, and the tracked tree must
   contain no secrets or audio.

## Tasks

- **T1 — Authentication and scope resolution.** Settings (`auth_enabled`,
  `api_keys`), `X-API-Key` → tenant scope, `401 UNAUTHORIZED` when missing or
  wrong, constant-time comparison. Tests: auth off ⇒ `local`; on ⇒ 401
  without/with a bad key; valid key ⇒ its scope.
- **T2 — Tenant isolation on every path.** Creation, retrieval, result,
  retry, cancel and the presigned-upload flow all use the authenticated
  scope; cross-tenant attempts produce `404`. Tests per endpoint.
- **T3 — Retention and deletion.** `DELETE /v1/transcriptions/{id}` (scoped,
  deletes objects + rows), `scripts/purge_expired.py` with `--dry-run` and
  `--older-than-days`, `STT_RETENTION_DAYS`; non-terminal jobs are never
  purged. Tests: object deletion, row deletion, expiry selection, dry-run.
- **T4 — Quotas and safe media limits.** Per-scope request quota
  (`STT_MAX_REQUESTS_PER_MINUTE`, `429 RATE_LIMITED`), ffprobe timeout for
  hostile media, and a test that a slow/huge probe cannot hang a request.
- **T5 — Load harness.** `scripts/load_test.py` — concurrency, request count,
  p50/p95/p99, throughput, error rate; real run against the real stack with
  the GPU provider, results in `results/phase7_load/`.
- **T6 — Failure-recovery tests.** Storage outage (job fails cleanly, retry
  succeeds once storage returns), provider outage, and a chaos e2e
  (`scripts/e2e_phase7.py`) that kills the worker mid-job, restarts it, and
  verifies recovery; API restart during polling.
- **T7 — Operations and incident recovery docs.** `docs/operations.md`
  (services, ports, start/stop, backups, scaling knobs, routine checks) and
  `docs/incident-recovery.md` (power cut, Postgres down, Temporal down,
  storage down, provider outage, disk full, stuck job, credential rotation),
  written from what was actually observed.
- **T8 — Security audit + report.** Bucket privacy (anonymous GET fails),
  secret hygiene test over the tracked tree, log-redaction audit with a real
  transcript, and `docs/phase-7-report.md`.

## Acceptance criteria

- Every task's tests were written first and witnessed failing.
- `uv run pytest -q`, `-m gpu`, `ruff`, frontend `npm test` + `npm run build`
  all green; load and chaos numbers recorded in `results/`.
- Auth on: no request without a key succeeds; no cross-tenant read succeeds;
  a deleted job's objects and rows are gone; a purge dry-run changes nothing.
- `docs/operations.md` and `docs/incident-recovery.md` describe the real
  system (ports, commands, failure modes observed), not an idealized one.

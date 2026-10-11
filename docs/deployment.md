# Deployment

The whole platform — database, object storage, workflow engine, API, worker and
console — runs from one compose file, started by one script.

```bash
./launch.sh          # start (builds on first run, generates .env if missing)
./launch.sh gpu      # same, with CUDA for the local model
./launch.sh status   # what is up, what is healthy, what it answered
```

---

## 1. What you need

| Requirement | Notes |
| --- | --- |
| Docker Engine or Docker Desktop | with the **compose v2** plugin (`docker compose`, not `docker-compose`) |
| ~8 GB free disk | images, plus the local model (0.5 GB `small`, 1.6 GB `large-v3-turbo`) |
| 8 GB RAM | CPU mode runs comfortably; GPU mode is faster and lighter on RAM |
| NVIDIA container runtime *(optional)* | only for `./launch.sh gpu` |

Nothing else. No Python, no Node, no PostgreSQL and no cloud account are needed
on the host: the API and worker images install their own dependencies, and the
console is a static bundle behind nginx.

**Check the GPU path before relying on it:**

```bash
docker run --rm --gpus all nvidia/cuda:12.4.1-base-ubuntu22.04 nvidia-smi
```

If that prints a GPU table, `./launch.sh gpu` will work. If it errors, stay on
CPU — the launcher detects this and tells you which mode it chose.

---

## 2. Build and run

```bash
git clone https://github.com/Abdul-Jaweed/stt-platform
cd stt-platform
chmod +x launch.sh        # once, on Linux/macOS
./launch.sh
```

On Windows use git-bash and the same command (`bash launch.sh` if the execute
bit is not set). Docker Desktop must be running.

What happens:

1. `.env` is created from `.env.example` if it is missing.
2. A **UI password** (24 random characters) and a **session secret** are
   generated into `.env` and printed once. They are never hardcoded and never
   committed — `.env` is gitignored.
3. Both images are built. Layers are cached, so this is slow only the first
   time (or after changing dependencies).
4. The stack comes up in dependency order: Postgres and object storage → schema
   migration → Temporal → API → worker → console.
5. The launcher waits for the API and the console to answer, then prints the
   URLs.

The first transcription downloads the local model into `./models` (mounted into
both the API and the worker) unless the cache is already warm. Watch it with
`./launch.sh logs api`.

### Verify it worked

```bash
./launch.sh status
curl -s http://127.0.0.1:8200/v1/health/ready
```

Then open <http://127.0.0.1:8080>, sign in with the credentials printed at
launch (also in `.env`), and transcribe something.

### Commands

| Command | What it does |
| --- | --- |
| `./launch.sh` | start everything |
| `./launch.sh gpu` | start with GPU passthrough for the local model |
| `./launch.sh mlflow` | add the MLflow tracking server and restart API + worker |
| `./launch.sh status` | containers, plus a live probe of each endpoint |
| `./launch.sh logs [svc]` | follow logs (all services, or one: `api`, `worker`, `frontend`) |
| `./launch.sh restart` | recreate the application containers |
| `./launch.sh rebuild` | rebuild both images, then restart |
| `./launch.sh stop` | stop everything, **keep** data |
| `./launch.sh clean` | delete containers, volumes and images (asks first) |

---

## 3. Services and ports

| Service | Container | Host port | What it is |
| --- | --- | --- | --- |
| `frontend` | `stt-frontend` | **8080** | the console (nginx), same-origin proxy to the API |
| `api` | `stt-api` | **8200** | FastAPI application, `/v1` + `/docs` |
| `postgres` | `stt-postgres` | 25432 | application database |
| `rustfs` | `stt-rustfs` | 29000 / 29001 | S3-compatible object storage (console on 29001) |
| `temporal` | `stt-temporal` | 7233 | workflow engine |
| `temporal-ui` | `stt-temporal-ui` | 28080 | workflow inspector |
| `mlflow` | `stt-mlflow` | 5000 | tracking server (profile `mlflow`, off by default) |

Inside the network the services use their own names and standard ports
(`postgres:5432`, `rustfs:9000`, `temporal:7233`, `api:8200`) — the host port
numbers above exist so you can reach them from your machine.

The console talks to the API through nginx on the **same origin**, so there is
no CORS configuration to get wrong and the session cookie is first-party.

> `infrastructure/docker-compose.yml` is the dev-only subset (database +
> storage + Temporal) used by the test suite. It binds the same host ports, so
> run one stack or the other, not both.

---

## 4. Configuration

Everything lives in `.env` (created from `.env.example`). Compose reads it
automatically; the application reads the same names with the `STT_` prefix.

### Required — generated for you

| Variable | Meaning |
| --- | --- |
| `STT_UI_USERNAME` | console login (default `stt`) |
| `STT_UI_PASSWORD` | console password, 24 random chars on first launch |
| `STT_SESSION_SECRET` | signs session cookies; keep it stable or every session dies on restart |

`STT_ENVIRONMENT=production` turns on a fail-closed guard: the API refuses to
start if the password is missing or weak, or if no session secret is set. That
is deliberate — a deployment should not come up on a known default.

### Common

| Variable | Default | Meaning |
| --- | --- | --- |
| `STT_WHISPER_MODEL` | `small` | local model; `large-v3-turbo` is the accuracy choice |
| `STT_DEVICE` | `cpu` | `cpu` or `cuda` (the GPU override sets `cuda`) |
| `STT_COMPUTE_TYPE` | `int8` | `int8` on CPU, `float16` on GPU |
| `STT_MAX_SYNC_DURATION_S` | `180` | uploads shorter than this are transcribed inside the request; longer ones become durable jobs the worker runs |
| `STT_SESSION_COOKIE_SECURE` | `false` | set `true` behind TLS (production sets it automatically when unset) |
| `STT_LOG_LEVEL` | `INFO` | log verbosity |
| `STT_MLFLOW_ENABLED` | `false` | log evaluations to MLflow |
| `STT_AUTH_ENABLED` / `STT_API_KEYS` | off | API keys for programmatic clients (`key:tenant` pairs) |

> **`STT_MAX_SYNC_DURATION_S=0` and evaluations.** Setting it to `0` forces
> every upload down the durable async path, which is useful for exercising the
> worker — but an evaluation transcribes each clip through the *synchronous*
> path, and `0` makes that path reject every clip (`AUDIO_TOO_LONG`). Keep a
> positive value if you intend to run evaluations.

### Managed providers

A managed provider registers **only when its key is present**, and nothing is
sent to a cloud provider unless a request explicitly selects one.

```bash
STT_DEEPGRAM_API_KEY=…
STT_ELEVENLABS_API_KEY=…
STT_SARVAM_API_KEY=…
```

The local model stays the default; these add options, they do not replace it.

### Changing a setting

```bash
$EDITOR .env
./launch.sh restart      # API + worker + console pick up the new values
```

---

## 5. GPU

```bash
./launch.sh gpu
```

This layers `docker-compose.gpu.yml` over the base file: it requests one NVIDIA
device for the API and the worker and switches the model to
`large-v3-turbo` / `float16`.

| Variable | Default | Meaning |
| --- | --- | --- |
| `STT_GPU_COUNT` | `1` | devices to request (`all` also works) |
| `STT_WHISPER_MODEL_GPU` | `large-v3-turbo` | the GPU model — deliberately its own variable, so the CPU default in `STT_WHISPER_MODEL` cannot shadow it |
| `STT_COMPUTE_TYPE_GPU` | `float16` | use `int8_float16` on pre-Turing cards |

The container keeps its own CUDA libraries; the host only needs a driver new
enough for the base image and the NVIDIA container runtime. Verify the GPU is
actually visible inside the container:

```bash
docker compose -f docker-compose.yml -f docker-compose.gpu.yml exec api nvidia-smi -L
```

If the runtime is missing, Docker refuses to start the container with a
`could not select device driver` error — that is the signal to install
`nvidia-container-toolkit` (Linux) or enable GPU support in Docker Desktop
(Windows/WSL2). The CPU path is unaffected.

---

## 6. Operations

### Logs

```bash
./launch.sh logs             # everything
./launch.sh logs api         # one service
docker compose logs -f worker --tail=200
```

Logs are plain text by default. `STT_LOG_JSON=true` switches to one redacted
JSON object per line; secrets and audio contents are never logged either way.

### Data

| What | Where |
| --- | --- |
| database | `./.data/postgres` (bind mount) |
| stored audio + artifacts | `./.data/rustfs` |
| Temporal's own database | `./.data/temporal-postgres` |
| models + evaluation corpus | `./models` |
| MLflow (when enabled) | `./.data/mlflow` |

`./launch.sh stop` keeps all of it. `./launch.sh clean` deletes all of it.

### Backup

```bash
docker compose exec postgres pg_dump -U stt stt > backup.sql   # metadata
tar -czf rustfs-backup.tgz .data/rustfs                        # audio objects
```

Both matter: the database holds the job records, the object store holds the
audio those records point at.

### Health

| Endpoint | What it answers |
| --- | --- |
| `GET /v1/health/live` | the process is up |
| `GET /v1/health/ready` | database, object storage and Temporal are reachable |
| `GET /v1/providers` | which providers are registered and what they claim |

Every container has a healthcheck; `docker compose ps` shows them, and
`./launch.sh status` adds a live probe from the host.

---

## 7. Troubleshooting

**`docker compose` is not a command.** You have the old standalone
`docker-compose`. Install the compose v2 plugin — the launcher checks this.

**A port is already in use.** Something else holds 8080/8200/25432/29000/7233/
28080/5000 — often the dev subset stack. Stop it
(`docker compose -f infrastructure/docker-compose.yml down`) or change the host
port in `docker-compose.yml`.

**The API restarts in a loop with a configuration error.** Read the message:
it is the production guard telling you which credential is missing or weak.
Set it in `.env` (`STT_UI_PASSWORD` must be at least 12 characters, or set
`STT_UI_PASSWORD_HASH`).

**Sign-in fails with `session_cookie_secure`.** You are on plain HTTP with
`STT_SESSION_COOKIE_SECURE=true`. Either serve over TLS or set it to `false`
for a local demo.

**First transcription is slow.** The model is downloading into `./models`.
`./launch.sh logs api` shows progress; afterwards it is cached.

**`could not select device driver "" with capabilities: [[gpu]]`.** You asked
for `gpu` without the NVIDIA container runtime. Install it, or start on CPU.

**Jobs stay `QUEUED`.** The worker is not running or cannot reach Temporal:
`docker compose ps worker` and `./launch.sh logs worker`.

**The console loads but every call fails.** The console proxies `/v1` to the
API container; if `stt-api` is unhealthy, requests fail with 502. Check
`curl http://127.0.0.1:8200/v1/health/ready`.

**Windows: `localhost` hangs.** Use `127.0.0.1` explicitly — on Windows,
`localhost` can resolve to `::1` while the published port is IPv4-only.

---

## 9. What was verified on this stack

Not a promise — the checks that were actually run, on Windows 11 with Docker
Desktop and an RTX 4060:

| Check | Result |
| --- | --- |
| `./launch.sh` (CPU default) | both images built, 8 containers up, `/v1/health/ready` → `{"status":"ready"}` |
| Console through nginx | `:8080` → 200, `/evaluations` deep link → 200 (SPA fallback), `/health` proxied → 200 |
| Sign-in | `POST /v1/auth/login` via `:8080` → 200; wrong password → 401; anonymous `/v1/auth/me` → 401 |
| Session cookie | `HttpOnly; SameSite=lax; Path=/`; `document.cookie` empty in the browser |
| Sync transcription (CPU `small`) | job SUCCEEDED — 1 segment, 24 word timings, `en` detected |
| Async path (`STT_MAX_SYNC_DURATION_S=0`) | 201 `QUEUED` → `PROCESSING` → `SUCCEEDED`, run by the worker container |
| Evaluation inside the stack | 8/8 clips scored, 0 failures, WER 0.117 (CPU `small`; the documented 0.100 is `large-v3-turbo`) |
| GPU passthrough (`./launch.sh gpu`) | `nvidia-smi -L` inside the container; `large-v3-turbo` on CUDA — RTF 0.62 cold, 0.099 warm |
| Browser walkthrough | landing page, theme toggle, sign-in → `/upload`, evaluations summary with live data |

Two things this run surfaced, both fixed in the images rather than documented
around: `repo_root` had to come from configuration instead of the package's
install path (a wheel install put it outside the application), and ctranslate2
needs the CUDA runtime libraries plus an explicit `LD_LIBRARY_PATH` — the slim
Python base image has neither.

## 10. What is *not* production-hardened

Stated plainly, because a deployment guide that oversells is worse than none:

- **TLS terminates outside this stack.** Run it behind a reverse proxy that
  does HTTPS and sets `STT_SESSION_COOKIE_SECURE=true`.
- **The rate limiter is in-process** (one API container). Scale the API out
  and quotas become per-replica — the platform documents this rather than
  pretending otherwise.
- **Credentials in `.env` are single-tenant.** `STT_API_KEYS` supports several
  keys, but the compose file ships one UI account.
- **Object storage credentials are the development defaults** unless you
  change them; the storage service is not exposed beyond localhost by default.

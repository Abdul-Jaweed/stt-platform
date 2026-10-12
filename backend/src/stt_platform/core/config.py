"""Application settings. Environment overrides use the STT_ prefix."""
import os
from functools import lru_cache
from pathlib import Path

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from stt_platform.services.auth import (
    MIN_PRODUCTION_PASSWORD_LENGTH,
    is_weak_password,
)

# backend/src/stt_platform/core/config.py -> repository root (local-stt/)
_REPO_ROOT = Path(__file__).resolve().parents[4]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="STT_", env_file=".env", extra="ignore"
    )

    repo_root: Path = _REPO_ROOT

    model_dir: Path | None = None
    hf_home: Path | None = None
    temp_dir: Path | None = None
    audio_store_dir: Path | None = None

    database_url: str = "postgresql+psycopg://stt:stt@127.0.0.1:25432/stt"
    whisper_model: str = "large-v3-turbo"
    device: str = "cuda"
    compute_type: str = "float16"

    max_upload_bytes: int = 100 * 1024 * 1024
    max_sync_duration_s: float = 180.0
    #: Live preview (while recording) re-transcribes the audio captured so far
    #: on every tick, so it is capped: past this, the client stops asking and
    #: the full job takes over.
    max_live_preview_duration_s: float = 120.0
    #: True streaming (Phase 8): a WebSocket session decodes the audio once and
    #: emits each utterance as it closes. The cap replaces the preview's because
    #: a stream is decoded once, not re-transcribed on every tick.
    stream_max_duration_s: float = 300.0
    #: A session that hears nothing for this long ends itself (reason "idle").
    stream_idle_timeout_s: float = 30.0
    #: Concurrent streams per API process -- a stream holds a GPU slot for its
    #: whole life, so the bound is small and the refusal explicit (STREAM_BUSY).
    stream_max_sessions: int = 2
    #: One WebSocket frame may not exceed this many bytes of audio.
    stream_max_frame_bytes: int = 1_048_576
    sync_duration_tolerance_s: float = 0.5
    #: Long audio is chunked inside the local provider: past this many
    #: seconds the file is decoded once, sliced at exact sample offsets,
    #: each slice is transcribed, and every slice is shifted back onto the
    #: original timeline. 0 disables chunking (single pass, the library
    #: default; the shipped compose stack sets 600).
    chunk_seconds: float = 0.0

    s3_endpoint: str = "http://127.0.0.1:29000"
    s3_access_key: str = "stt"
    s3_secret_key: str = "stt123456"
    s3_region: str = "us-east-1"
    s3_bucket_audio: str = "stt-audio"
    s3_bucket_artifacts: str = "stt-artifacts"
    presign_expiry_s: int = 900

    temporal_host: str = "127.0.0.1:7233"
    temporal_task_queue: str = "stt-transcription"
    gpu_concurrency: int = 1
    managed_concurrency: int = 4
    remote_poll_interval_s: float = 5.0
    remote_max_wait_s: float = 7200.0
    dispatch_poll_interval_s: float = 2.0
    retry_max_attempts: int = 3
    retry_base_delay_s: float = 2.0
    retry_multiplier: float = 2.0
    retry_max_delay_s: float = 60.0
    retry_jitter_ratio: float = 0.25
    max_async_duration_s: float = 14400.0

    # Managed providers. Keys are optional: a provider registers only when
    # its key is set (STT_DEEPGRAM_API_KEY / STT_ELEVENLABS_API_KEY /
    # STT_SARVAM_API_KEY). Base URLs are overridable for fake servers.
    deepgram_api_key: str | None = None
    deepgram_base_url: str = "https://api.deepgram.com"
    elevenlabs_api_key: str | None = None
    elevenlabs_base_url: str = "https://api.elevenlabs.io"
    elevenlabs_model_id: str = "scribe_v2"
    sarvam_api_key: str | None = None
    sarvam_base_url: str = "https://api.sarvam.ai"
    provider_timeout_s: float = 300.0
    provider_connect_timeout_s: float = 10.0
    max_provider_response_bytes: int = 25_000_000

    # Observability. ON by default: instrumentation should not need to be
    # discovered before it can help. The endpoint is optional by contract --
    # with no collector listening, exports fail quietly and requests are
    # untouched -- and ``STT_OTEL_ENABLED=false`` restores the no-op
    # provider for anyone who wants zero overhead. "otlp" posts to
    # otel_endpoint (OTLP/HTTP); "console" prints spans; "none" records
    # nothing but keeps instrumentation call sites identical.
    otel_enabled: bool = True
    otel_exporter: str = "otlp"
    otel_endpoint: str = "http://127.0.0.1:4318"
    otel_service_name: str = "stt-platform"
    otel_environment: str = "local"
    otel_sample_ratio: float = 1.0
    # Recovery bound (Phase 7): how long one provider attempt may run before
    # Temporal retries it. A worker killed mid-inference recovers in at most
    # this long instead of the old 4 h; raise it for very long audio.
    activity_attempt_timeout_s: int = 1800
    # Security (Phase 7). Off by default so the local developer flow keeps
    # working; with it on, every /v1 request must present a key and the
    # key's tenant — never the request — decides the scope.
    auth_enabled: bool = False
    api_keys: str = ""
    max_requests_per_minute: int = 0
    retention_days: int = 0
    #: Deployment profile. "production" refuses the development defaults
    #: below instead of quietly shipping them.
    environment: str = "local"
    # UI login. The browser holds an HttpOnly session cookie, never a key or a
    # password; the server decides which tenant that session acts as.
    ui_auth_enabled: bool = True
    ui_username: str = "stt"
    #: Development default only — production must set a strong password or,
    #: better, a hash (see ``ui_password_hash``).
    ui_password: str | None = "stt"
    ui_password_hash: str | None = None
    #: The tenant a UI session acts as. Server-assigned: a session, like a
    #: key, never lets the caller pick its own scope.
    ui_tenant: str = "local"
    #: Signs session tokens. Unset means "generate one per process": sessions
    #: then do not survive a restart (fine locally, refused in production).
    session_secret: str | None = None
    session_ttl_s: int = 12 * 3600
    session_cookie_name: str = "stt_session"
    #: Secure cookies require HTTPS. Defaults to on in production; the local
    #: dev flow is plain HTTP on 127.0.0.1, where a Secure cookie is never sent.
    session_cookie_secure: bool = False
    #: Failed logins per minute, per (address, account). 0 disables it.
    login_max_attempts_per_minute: int = 10
    #: The login page's demo panel: fills the username, and the password too
    #: when it is the published development default — a generated password is
    #: named, never sent. Forced off in production whatever this says; turn it
    #: off for any deployment other people can reach.
    auth_demo_mode: bool = True
    # Evaluation (Phase 6): where datasets and their audio live, how large a
    # run may be, and where MLflow keeps the record of it. MLflow logging is
    # best-effort and time-bounded: a dead tracking server downgrades the
    # evaluation's ``mlflow`` field to an error, never the evaluation itself.
    evaluation_datasets_dir: Path | None = None
    evaluation_audio_root: Path | None = None
    evaluation_max_clips: int = 25
    mlflow_enabled: bool = True
    mlflow_tracking_uri: str = "http://127.0.0.1:5000"
    mlflow_experiment: str = "stt-evaluation"
    mlflow_timeout_s: float = 10.0
    # Runtime sampler interval for CPU/RSS/GPU/queue gauges.
    runtime_sample_interval_s: float = 15.0
    # Logs: plain text by default (readable locally); "json" switches the
    # root handler to one redacted JSON object per line for aggregation.
    log_json: bool = False
    log_level: str = "INFO"
    # Prometheus scrape endpoint for the metric reader (T7).
    prometheus_enabled: bool = False
    prometheus_port: int = 9464
    # Runtime sampler interval (CPU/RSS/GPU/queue depth).
    runtime_sampler_interval_s: float = 15.0

    # Browser frontend origins (Vite dev server / preview server). Override
    # with a JSON array in STT_CORS_ORIGINS.
    cors_origins: list[str] = [
        "http://127.0.0.1:5173",
        "http://127.0.0.1:4173",
    ]

    @model_validator(mode="after")
    def _guard_production_credentials(self) -> "Settings":
        """Fail closed: a production deployment may not run on the defaults."""
        if self.environment != "production":
            return self
        # Secure cookies unless the deployment explicitly says otherwise
        # (a TLS-terminating proxy can legitimately need the opt-out).
        if "session_cookie_secure" not in self.model_fields_set:
            self.session_cookie_secure = True
        if self.ui_auth_enabled:
            if not self.ui_password_hash and not self.ui_password:
                raise ValueError(
                    "production needs UI credentials: set STT_UI_PASSWORD_HASH "
                    "(preferred) or STT_UI_PASSWORD"
                )
            if not self.ui_password_hash and is_weak_password(self.ui_password):
                raise ValueError(
                    "production requires a UI password of at least "
                    f"{MIN_PRODUCTION_PASSWORD_LENGTH} characters, or a "
                    "precomputed STT_UI_PASSWORD_HASH"
                )
            if not self.session_secret:
                raise ValueError(
                    "production needs STT_SESSION_SECRET: without it every "
                    "restart invalidates every session and a multi-replica "
                    "deployment cannot verify its own tokens"
                )
        return self

    @model_validator(mode="after")
    def _fill_path_defaults(self) -> "Settings":
        if self.model_dir is None:
            self.model_dir = self.repo_root / "models" / "faster-whisper"
        if self.hf_home is None:
            self.hf_home = self.repo_root / "models" / "huggingface"
        if self.temp_dir is None:
            self.temp_dir = self.repo_root / ".data" / "tmp"
        if self.audio_store_dir is None:
            self.audio_store_dir = self.repo_root / ".data" / "audio"
        return self

    def apply_runtime_env(self) -> None:
        """Point ML caches at the I: drive and create runtime directories."""
        os.environ["HF_HOME"] = str(self.hf_home)
        os.environ["HF_HUB_CACHE"] = str(self.hf_home / "hub")
        os.environ["HF_HUB_DISABLE_SYMLINKS_WARNING"] = "1"
        os.environ["UV_CACHE_DIR"] = str(self.repo_root / ".cache" / "uv")
        for path in (self.temp_dir, self.audio_store_dir):
            path.mkdir(parents=True, exist_ok=True)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()

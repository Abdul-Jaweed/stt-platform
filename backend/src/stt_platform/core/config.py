"""Application settings. Environment overrides use the STT_ prefix."""
import os
from functools import lru_cache
from pathlib import Path

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

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
    sync_duration_tolerance_s: float = 0.5

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

    # Observability (Phase 5). Off by default: a no-op provider means zero
    # overhead and no new failure mode. "otlp" posts to otel_endpoint
    # (OTLP/HTTP); "console" prints spans; "none" records nothing.
    otel_enabled: bool = False
    otel_exporter: str = "otlp"
    otel_endpoint: str = "http://127.0.0.1:4318"
    otel_service_name: str = "stt-platform"
    otel_environment: str = "local"
    otel_sample_ratio: float = 1.0
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

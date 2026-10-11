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

    temporal_host: str = "127.0.0.1:7233"
    temporal_task_queue: str = "stt-transcription"
    gpu_concurrency: int = 1
    dispatch_poll_interval_s: float = 2.0
    retry_max_attempts: int = 3
    retry_base_delay_s: float = 2.0
    retry_multiplier: float = 2.0
    retry_max_delay_s: float = 60.0
    retry_jitter_ratio: float = 0.25
    max_async_duration_s: float = 14400.0

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

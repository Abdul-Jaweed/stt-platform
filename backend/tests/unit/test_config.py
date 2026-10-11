"""Settings resolve inside the repository and honor STT_ env overrides."""
import os

from stt_platform.core.config import Settings, get_settings


def test_default_paths_resolve_inside_repo():
    s = Settings(_env_file=None)
    assert s.model_dir == s.repo_root / "models" / "faster-whisper"
    assert s.hf_home == s.repo_root / "models" / "huggingface"
    assert s.temp_dir == s.repo_root / ".data" / "tmp"
    assert s.database_url == "postgresql+psycopg://stt:stt@127.0.0.1:25432/stt"


def test_env_override_wins(monkeypatch):
    monkeypatch.setenv("STT_DATABASE_URL", "postgresql+psycopg://x@127.0.0.1:1/x")
    s = Settings(_env_file=None)
    assert s.database_url == "postgresql+psycopg://x@127.0.0.1:1/x"


def test_apply_runtime_env_sets_hf_cache_and_dirs(tmp_path):
    s = Settings(
        _env_file=None,
        repo_root=tmp_path,
        temp_dir=tmp_path / "t",
        audio_store_dir=tmp_path / "a",
    )
    s.apply_runtime_env()
    assert os.environ["HF_HOME"] == str(tmp_path / "models" / "huggingface")
    assert os.environ["HF_HUB_CACHE"] == str(tmp_path / "models" / "huggingface" / "hub")
    assert (tmp_path / "t").is_dir()
    assert (tmp_path / "a").is_dir()


def test_get_settings_is_cached():
    get_settings.cache_clear()
    assert get_settings() is get_settings()

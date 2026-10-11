import pytest

from stt_platform.audio.validation import validate_upload
from stt_platform.core.config import Settings
from stt_platform.domain.errors import AudioValidationError, AudioTooLong, UploadTooLarge
from tests.audio_fixtures import make_wav


def _settings(tmp_path, **overrides) -> Settings:
    values = dict(
        _env_file=None,
        repo_root=tmp_path,
        temp_dir=tmp_path / "t",
        audio_store_dir=tmp_path / "a",
    )
    values.update(overrides)
    return Settings(**values)


@pytest.fixture
def settings(tmp_path):
    return _settings(tmp_path, max_sync_duration_s=1.0)


def test_accepts_audio_within_limits(tmp_path, settings):
    wav = make_wav(tmp_path, seconds=0.9)
    info = validate_upload(wav, settings)
    assert info.duration_s < 1.0
    assert info.format_name == "wav"


def test_rejects_long_audio(tmp_path, settings):
    wav = make_wav(tmp_path, seconds=2.0)
    with pytest.raises(AudioTooLong):
        validate_upload(wav, settings)


def test_size_limit_checked_before_probe(tmp_path, monkeypatch):
    small_limit = _settings(tmp_path, max_upload_bytes=1000)
    from stt_platform.audio import validation as v

    monkeypatch.setattr(
        v, "inspect_audio", lambda *a, **k: pytest.fail("probe must not run")
    )
    big = tmp_path / "big.bin"
    big.write_bytes(b"\0" * 2000)
    with pytest.raises(UploadTooLarge):
        validate_upload(big, small_limit)


def test_rejects_missing_file(tmp_path, settings):
    with pytest.raises(AudioValidationError):
        validate_upload(tmp_path / "absent.wav", settings)


def test_extension_never_trusted_for_valid_content(tmp_path, settings):
    # a valid tiny wav named .mp3 must be accepted based on real content
    wav = make_wav(tmp_path, seconds=0.9)
    misnamed = tmp_path / "sneaky.mp3"
    wav.rename(misnamed)
    info = validate_upload(misnamed, settings)
    assert info.format_name == "wav"

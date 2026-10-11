"""Live preview: local-only, duration-bounded, and never persisted.

The preview exists so a recording browser can show text while the user is
still speaking. It is deliberately NOT a job: no row, no object, no trail —
and it never touches a managed provider, because a preview must not spend
someone's paid quota by surprise.
"""
import pytest

from stt_platform.core.config import Settings
from stt_platform.domain.errors import AudioTooLong, LivePreviewUnsupported
from stt_platform.services.live import preview_transcription
from tests.audio_fixtures import make_wav
from tests.fakes import FakeAsyncProvider, FakeProvider


def _settings(tmp_path, **overrides):
    values = dict(
        _env_file=None,
        repo_root=tmp_path,
        temp_dir=tmp_path / "t",
        audio_store_dir=tmp_path / "a",
        database_url="postgresql+psycopg://u:p@127.0.0.1:1/db?connect_timeout=1",
    )
    values.update(overrides)
    return Settings(**values)


def test_preview_returns_text_and_is_explicitly_not_final(tmp_path):
    wav = make_wav(tmp_path, seconds=2.0)

    preview = preview_transcription(
        audio_path=wav,
        provider=FakeProvider(),
        language="en",
        settings=_settings(tmp_path),
    )

    assert preview.text.strip()
    assert preview.is_final is False
    assert preview.provider == "fake"
    assert preview.model
    assert 1.8 < preview.audio_duration_s < 2.2
    assert preview.processing_duration_s >= 0
    assert preview.segments


def test_preview_refuses_a_managed_provider(tmp_path):
    wav = make_wav(tmp_path, seconds=2.0)

    with pytest.raises(LivePreviewUnsupported) as excinfo:
        preview_transcription(
            audio_path=wav,
            provider=FakeAsyncProvider(),
            language="en",
            settings=_settings(tmp_path),
        )

    assert excinfo.value.code == "LIVE_PREVIEW_LOCAL_ONLY"
    assert "fake-async" in excinfo.value.message


def test_preview_refuses_audio_over_the_cap(tmp_path):
    wav = make_wav(tmp_path, seconds=3.0)

    with pytest.raises(AudioTooLong) as excinfo:
        preview_transcription(
            audio_path=wav,
            provider=FakeProvider(),
            language="en",
            settings=_settings(tmp_path, max_live_preview_duration_s=1.0),
        )

    assert excinfo.value.code == "AUDIO_TOO_LONG"
    assert "preview" in excinfo.value.message


def test_preview_rejects_undecodable_audio(tmp_path):
    bogus = tmp_path / "not-audio.wav"
    bogus.write_bytes(b"nope " * 100)

    with pytest.raises(Exception) as excinfo:
        preview_transcription(
            audio_path=bogus,
            provider=FakeProvider(),
            language="en",
            settings=_settings(tmp_path),
        )

    assert getattr(excinfo.value, "code", None) == "UNDECODABLE"

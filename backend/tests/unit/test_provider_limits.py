"""Provider-declared limits: checked before a vendor call, never after.

Every populated limit traces to the vendor's own documentation
(``docs/providers.md`` records the source); a provider that documents nothing
declares ``None`` and this module refuses nothing on its behalf.
"""
import pytest

from stt_platform.audio.inspect import AudioInfo
from stt_platform.audio.limits import check_provider_limits
from stt_platform.domain.errors import AudioTooLong, UploadTooLarge
from stt_platform.schemas.providers import ProviderCapabilities


def _info(*, duration_s: float = 1.0, size_bytes: int = 1000) -> AudioInfo:
    return AudioInfo(
        format_name="wav",
        codec="pcm_s16le",
        sample_rate=16000,
        channels=1,
        duration_s=duration_s,
        size_bytes=size_bytes,
    )


def test_audio_beyond_the_provider_duration_cap_is_refused():
    caps = ProviderCapabilities(max_duration_s=7200.0)
    with pytest.raises(AudioTooLong) as exc:
        check_provider_limits(_info(duration_s=8000.0), caps, provider_id="sarvam")
    assert "sarvam" in exc.value.message
    assert "7200" in exc.value.message


def test_a_file_beyond_the_provider_size_cap_is_refused():
    caps = ProviderCapabilities(max_file_bytes=3 * 1024**3)
    with pytest.raises(UploadTooLarge) as exc:
        check_provider_limits(
            _info(size_bytes=4 * 1024**3), caps, provider_id="elevenlabs"
        )
    assert "elevenlabs" in exc.value.message


def test_the_documented_cap_itself_is_allowed():
    caps = ProviderCapabilities(max_duration_s=30.0, max_file_bytes=1000)
    check_provider_limits(_info(duration_s=30.0, size_bytes=1000), caps, provider_id="sarvam")


def test_undocumented_limits_are_not_invented():
    """No vendor number means no refusal: the platform never guesses."""
    check_provider_limits(
        _info(duration_s=10 * 3600.0, size_bytes=10**12),
        ProviderCapabilities(),
        provider_id="faster-whisper",
    )

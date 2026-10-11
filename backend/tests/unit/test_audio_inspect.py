import pytest

from stt_platform.audio.inspect import inspect_audio
from stt_platform.domain.errors import AudioValidationError
from tests.audio_fixtures import make_mp3, make_streaming_webm, make_wav


def test_reads_real_wav_content(tmp_path):
    wav = make_wav(tmp_path, seconds=2.0)
    info = inspect_audio(wav)
    assert info.format_name == "wav"
    assert info.sample_rate == 16000
    assert info.channels == 1
    assert 1.8 < info.duration_s < 2.2
    assert info.size_bytes > 1000
    assert info.codec is not None


def test_reads_content_regardless_of_extension(tmp_path):
    wav = make_wav(tmp_path, seconds=2.0)
    misnamed = tmp_path / "audio.bin"
    wav.rename(misnamed)
    info = inspect_audio(misnamed)
    assert info.format_name == "wav"
    assert info.sample_rate == 16000


def test_reads_mp3(tmp_path):
    mp3 = make_mp3(tmp_path, seconds=2.0)
    info = inspect_audio(mp3)
    assert info.format_name == "mp3"
    assert 1.5 < info.duration_s < 2.4


def test_rejects_text_bytes_with_wav_extension(tmp_path):
    p = tmp_path / "bomb.wav"
    p.write_bytes(b"definitely not audio at all " * 20)
    with pytest.raises(AudioValidationError) as excinfo:
        inspect_audio(p)
    assert excinfo.value.code == "UNDECODABLE"


def test_rejects_empty_file(tmp_path):
    p = tmp_path / "empty.wav"
    p.write_bytes(b"")
    with pytest.raises(AudioValidationError) as excinfo:
        inspect_audio(p)
    assert excinfo.value.code == "EMPTY"


def test_rejects_missing_file(tmp_path):
    with pytest.raises(AudioValidationError):
        inspect_audio(tmp_path / "nope.wav")


def test_measures_a_streamed_webm_without_a_duration_header(tmp_path):
    """Browser recordings have no duration element; measure, never reject."""
    webm = make_streaming_webm(tmp_path, seconds=3.0)

    info = inspect_audio(webm)

    assert info.format_name.startswith("matroska")
    assert info.codec == "opus"
    assert 2.7 < info.duration_s < 3.3


def test_streamed_webm_is_not_reported_as_zero_duration(tmp_path):
    webm = make_streaming_webm(tmp_path, seconds=1.5)

    info = inspect_audio(webm)

    assert info.duration_s > 1.2

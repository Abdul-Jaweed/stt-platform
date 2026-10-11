import pytest

from stt_platform.audio.inspect import inspect_audio
from stt_platform.domain.errors import AudioValidationError
from tests.audio_fixtures import make_mp3, make_wav


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

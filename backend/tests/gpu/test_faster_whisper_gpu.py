"""GPU integration tests: the real large-v3-turbo on CUDA.

Opt-in: ``uv run pytest -m gpu`` (excluded from the default run).
Requires a CUDA device and audio/sample.wav (owner's recording, never
committed). Evidence is written to results/phase1_gpu_smoke.json.
"""
import json
import time
from pathlib import Path

import pytest

from stt_platform.core.config import get_settings
from stt_platform.providers.base import TranscriptionRequest
from stt_platform.providers.faster_whisper import FasterWhisperProvider

REPO_ROOT = Path(__file__).resolve().parents[3]
SAMPLE = REPO_ROOT / "audio" / "sample.wav"
RESULTS_DIR = REPO_ROOT / "results"


def _cuda_available() -> bool:
    try:
        import ctranslate2

        return ctranslate2.get_cuda_device_count() > 0
    except Exception:
        return False


_skip_reasons = []
if not _cuda_available():
    _skip_reasons.append("no CUDA device available")
if not SAMPLE.is_file():
    _skip_reasons.append("audio/sample.wav missing")

pytestmark = [
    pytest.mark.gpu,
    pytest.mark.skipif(bool(_skip_reasons), reason="; ".join(_skip_reasons)),
]


@pytest.fixture(scope="module")
def adapter() -> FasterWhisperProvider:
    return FasterWhisperProvider(get_settings())


def test_gpu_transcribes_sample_with_word_timestamps(adapter):
    started = time.perf_counter()
    tr = adapter.transcribe(TranscriptionRequest(audio_path=SAMPLE))
    elapsed = time.perf_counter() - started

    assert tr.language == "en"
    assert tr.segments, "expected at least one segment"
    assert tr.words_available is True
    starts = [s.start for s in tr.segments]
    assert starts == sorted(starts)
    assert all(0 <= s.start < s.end <= tr.audio_duration_s + 0.5 for s in tr.segments)
    assert tr.text.strip()

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    (RESULTS_DIR / "phase1_gpu_smoke.json").write_text(
        json.dumps(
            {
                "elapsed_s": round(elapsed, 3),
                "audio_duration_s": tr.audio_duration_s,
                "processing_duration_s": tr.processing_duration_s,
                "rtf": round(tr.processing_duration_s / tr.audio_duration_s, 4),
                "segments": len(tr.segments),
                "words": sum(len(s.words) for s in tr.segments),
                "language": tr.language,
                "language_probability": tr.language_probability,
                "model": tr.model,
                "text_head": tr.text[:120],
            },
            indent=2,
        ),
        encoding="utf-8",
    )


def test_gpu_engine_is_loaded_once(adapter):
    before = adapter._engine
    adapter.transcribe(TranscriptionRequest(audio_path=SAMPLE))
    assert adapter._engine is before

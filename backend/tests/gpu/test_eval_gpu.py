"""GPU integration: the real corpus through large-v3-turbo, scored for real.

Opt-in: ``uv run pytest -m gpu``. Requires a CUDA device and the generated
corpus (``uv run python ../scripts/make_eval_corpus.py``); the corpus is
never committed, so the test skips honestly when it is absent.

This is the end-to-end proof of the phase: real model output, real WER/CER
against by-construction ground truth, real timestamp measurements.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from stt_platform.core.config import get_settings
from stt_platform.evaluation.datasets import load_dataset
from stt_platform.evaluation.runner import ClipOutcome, run_evaluation
from stt_platform.providers.base import TranscriptionRequest
from stt_platform.providers.faster_whisper import FasterWhisperProvider

pytestmark = pytest.mark.gpu

REPO_ROOT = Path(__file__).resolve().parents[3]
RESULTS_DIR = REPO_ROOT / "results"


def _provider() -> FasterWhisperProvider:
    settings = get_settings()
    return FasterWhisperProvider(settings)


def _transcribe(provider: FasterWhisperProvider, clip) -> ClipOutcome:
    import time

    started = time.time()
    transcript = provider.transcribe(
        TranscriptionRequest(audio_path=clip.audio, language=clip.language)
    )
    elapsed = time.time() - started
    return ClipOutcome(
        text=transcript.text,
        segments=tuple(transcript.segments),
        granularity="word" if transcript.words_available else "segment",
        timings={
            "queue_wait_s": 0.0,
            "processing_s": transcript.processing_duration_s,
            "end_to_end_s": elapsed,
            "real_time_factor": (
                (transcript.processing_duration_s or elapsed) / clip.duration_s
                if clip.duration_s
                else None
            ),
        },
        attempts=1,
        retries=0,
        provider=provider.id,
        model=transcript.model or "large-v3-turbo",
    )


def test_real_corpus_is_scored_end_to_end():
    dataset = load_dataset(
        REPO_ROOT
        / "backend"
        / "src"
        / "stt_platform"
        / "evaluation"
        / "datasets"
        / "stt-eval-core.json"
    )
    if dataset.missing_audio:
        pytest.skip(
            f"corpus not generated ({len(dataset.missing_audio)} clip(s) missing): "
            "run scripts/make_eval_corpus.py"
        )

    provider = _provider()
    result = run_evaluation(
        dataset=dataset,
        transcribe=lambda clip: _transcribe(provider, clip),
        provider="faster-whisper",
        model="large-v3-turbo",
        decoding={"language": "en", "provider": "faster-whisper"},
    )

    aggregate = result.aggregate
    assert aggregate["clips_total"] == len(dataset.scored_clips()) >= 2
    assert aggregate["failures"] == 0
    assert aggregate["wer_micro"] is not None
    # Clean synthetic speech: a real model must be near-perfect, and the
    # bound is loose on purpose so the test fails on breakage, not on noise.
    assert aggregate["wer_micro"] <= 0.25, aggregate
    assert aggregate["cer_micro"] <= 0.15, aggregate
    assert aggregate["segment_availability_mean"] == 1.0
    assert aggregate["word_availability_mean"] == 1.0
    assert aggregate["coverage_mean"] is not None
    assert aggregate["coverage_mean"] >= 0.8
    assert aggregate["rtf_mean"] is not None and aggregate["rtf_mean"] < 1.0

    RESULTS_DIR.mkdir(exist_ok=True)
    (RESULTS_DIR / "phase6_gpu_eval.json").write_text(
        json.dumps(result.as_dict(), indent=2), encoding="utf-8"
    )

"""The live-provider suite must be opt-in: deselected by default gates and
collectible only when explicitly asked for.
"""
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[2]


def _collect(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "pytest", *args, "--collect-only", "-q"],
        cwd=BACKEND,
        capture_output=True,
        text=True,
        timeout=120,
    )


def test_live_tests_are_deselected_in_default_gates():
    result = _collect()
    assert result.returncode == 0, result.stderr[-500:]
    assert "test_deepgram_live_word_timestamps" not in result.stdout
    assert "test_sarvam_live_batch_job" not in result.stdout


def test_live_tests_are_collected_when_explicitly_selected():
    result = _collect("-m", "live_provider")
    assert result.returncode == 0, result.stderr[-500:]
    assert "test_deepgram_live_word_timestamps" in result.stdout
    assert "test_elevenlabs_live_word_timestamps" in result.stdout
    assert "test_sarvam_live_batch_job" in result.stdout

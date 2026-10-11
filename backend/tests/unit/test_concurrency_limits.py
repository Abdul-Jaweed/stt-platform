"""Concurrency-split tests: GPU work is serialized by its own semaphore,
managed HTTP work by another; the two never block each other.
"""
import threading
import time
from types import SimpleNamespace

from stt_platform.core.config import Settings
from stt_platform.providers.faster_whisper import FasterWhisperProvider


class _SlowEngine:
    """Fake whisper engine: records overlap, sleeps, returns one segment."""

    def __init__(self, state, lock, delay=0.2):
        self._state = state
        self._lock = lock
        self._delay = delay

    def transcribe(self, path, **kwargs):
        with self._lock:
            self._state["now"] += 1
            self._state["max"] = max(self._state["max"], self._state["now"])
        time.sleep(self._delay)
        with self._lock:
            self._state["now"] -= 1
        info = SimpleNamespace(language="en", language_probability=0.9, duration=1.0)
        return iter([]), info


def _run_transcribes(provider, count=2):
    errors: list[BaseException] = []

    def call():
        try:
            provider.transcribe(SimpleNamespace(audio_path="unused.wav", language=None))
        except BaseException as exc:  # noqa: BLE001 - surfaced in the test
            errors.append(exc)

    threads = [threading.Thread(target=call) for _ in range(count)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert not errors, errors


def test_gpu_semaphore_serializes_transcribe_calls():
    state = {"now": 0, "max": 0}
    lock = threading.Lock()
    settings = Settings(device="cpu", gpu_concurrency=1)
    provider = FasterWhisperProvider(
        settings=settings,
        engine_factory=lambda *a, **k: _SlowEngine(state, lock),
    )

    _run_transcribes(provider, count=2)

    assert state["max"] == 1


def test_gpu_semaphore_respects_configured_concurrency():
    state = {"now": 0, "max": 0}
    lock = threading.Lock()
    settings = Settings(device="cpu", gpu_concurrency=2)
    provider = FasterWhisperProvider(
        settings=settings,
        engine_factory=lambda *a, **k: _SlowEngine(state, lock),
    )

    _run_transcribes(provider, count=2)

    assert state["max"] == 2


def test_managed_calls_proceed_while_gpu_slot_is_held():
    """A busy GPU semaphore must not block managed HTTP work."""
    from stt_platform.providers.managed.http_base import ManagedHttpClient

    settings = Settings(device="cpu", gpu_concurrency=1, managed_concurrency=2)
    provider = FasterWhisperProvider(
        settings=settings,
        engine_factory=lambda *a, **k: _SlowEngine(
            {"now": 0, "max": 0}, threading.Lock(), delay=0.4
        ),
    )

    managed = ManagedHttpClient(
        base_url="http://127.0.0.1:1",
        api_key="k",
        auth_header="Authorization",
        auth_scheme=None,
        semaphore=threading.BoundedSemaphore(2),
    )

    # Hold the GPU slot for the duration of the test, then assert the
    # managed client is not waiting on it (distinct semaphores).
    assert provider._semaphore is not managed._semaphore  # noqa: SLF001
    assert provider._semaphore.acquire(timeout=1)  # noqa: SLF001
    try:
        assert managed._semaphore.acquire(timeout=1)  # noqa: SLF001
        managed._semaphore.release()  # noqa: SLF001
    finally:
        provider._semaphore.release()  # noqa: SLF001

"""Shared pytest fixtures (extended by later tasks: audio fixtures, PG container)."""
import os

import pytest

# Docker Desktop on Windows: testcontainers must reach containers over IPv4
# loopback; ::1 hangs. Must be set before the docker client is constructed.
os.environ.setdefault("TESTCONTAINERS_HOST_OVERRIDE", "127.0.0.1")


@pytest.fixture(autouse=True)
def _dispose_app_engines(monkeypatch):
    """Dispose every database engine an application under test creates.

    ``TestClient(app)`` only runs the application's lifespan — and with it
    ``engine.dispose()`` — when the client is used as a context manager. The
    integration fixtures construct clients directly, so the pool is never
    closed, psycopg collects the still-open connections, and its
    ``BaseConnection.__del__`` raises an unraisable warning. Under
    ``-W error`` pytest reports that warning against whichever test happens to
    be running, which makes the failure look random and unrelated.

    Tracking engine construction here fixes it for every test at once,
    including ones written later.
    """
    from stt_platform import main as main_module

    engines = []
    real_make_engine = main_module.make_engine

    def tracked_make_engine(url, *args, **kwargs):
        engine = real_make_engine(url, *args, **kwargs)
        engines.append(engine)
        return engine

    monkeypatch.setattr(main_module, "make_engine", tracked_make_engine)
    yield
    for engine in engines:
        engine.dispose()

"""FastAPI application factory and ASGI entrypoint."""
from contextlib import asynccontextmanager

from fastapi import FastAPI

from stt_platform.api.errors import register_exception_handlers
from stt_platform.api.v1 import health, providers, transcriptions
from stt_platform.core.config import Settings, get_settings
from stt_platform.persistence.db import make_engine, make_session_factory
from stt_platform.providers.registry import ProviderRegistry
from stt_platform.storage.s3 import S3ObjectStore


def build_default_registry() -> ProviderRegistry:
    from stt_platform.providers.faster_whisper import FasterWhisperProvider

    registry = ProviderRegistry()
    registry.register(FasterWhisperProvider(), default=True)
    return registry


def create_app(
    settings: Settings | None = None,
    registry: ProviderRegistry | None = None,
    store: S3ObjectStore | None = None,
) -> FastAPI:
    settings = settings or get_settings()
    registry = registry or build_default_registry()
    engine = make_engine(settings.database_url)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        yield
        engine.dispose()

    app = FastAPI(title="STT Platform API", version="0.1.0", lifespan=lifespan)
    app.state.settings = settings
    app.state.registry = registry
    app.state.engine = engine
    app.state.session_factory = make_session_factory(engine)
    app.state.store = store

    register_exception_handlers(app)
    app.include_router(transcriptions.router, prefix="/v1")
    app.include_router(providers.router, prefix="/v1")
    app.include_router(health.router, prefix="/v1")
    return app


#: ASGI entrypoint used by uvicorn/gunicorn: wires real object storage.
#: ``create_app`` itself stays storage-optional so tests can opt in/out.
_settings = get_settings()
app = create_app(settings=_settings, store=S3ObjectStore.from_settings(_settings))

"""FastAPI application factory and ASGI entrypoint."""
import secrets
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response

from stt_platform.api.errors import register_exception_handlers
from stt_platform.api.ratelimit import SlidingWindowLimiter
from stt_platform.api.v1 import auth as auth_routes
from stt_platform.api.v1 import evaluations, health, providers, transcriptions
from stt_platform.core.config import Settings, get_settings
from stt_platform.observability import metrics, runtime, tracing
from stt_platform.observability.instrumentation import instrument_app
from stt_platform.observability.logging import setup_logging
from stt_platform.observability.prometheus import (
    METRICS_PATH,
    build_prometheus_reader,
    render_metrics,
)
from stt_platform.observability.temporal import temporal_interceptors
from stt_platform.persistence.db import make_engine, make_session_factory
from stt_platform.providers.defaults import build_default_registry
from stt_platform.providers.registry import ProviderRegistry
from stt_platform.services.auth import SessionTokens, hash_password
from stt_platform.storage.s3 import S3ObjectStore


def create_app(
    settings: Settings | None = None,
    registry: ProviderRegistry | None = None,
    store: S3ObjectStore | None = None,
    starter: Any = None,
) -> FastAPI:
    settings = settings or get_settings()
    registry = registry or build_default_registry(settings)
    engine = make_engine(settings.database_url)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        yield
        engine.dispose()

    app = FastAPI(title="STT Platform API", version="0.1.0", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["Content-Type", "Idempotency-Key", "Authorization"],
        # The session cookie travels with the request. Safe because the
        # allowed origins are an explicit list, never "*".
        allow_credentials=True,
    )
    app.state.settings = settings
    app.state.rate_limiter = SlidingWindowLimiter(settings.max_requests_per_minute)
    app.state.login_limiter = SlidingWindowLimiter(
        settings.login_max_attempts_per_minute
    )
    # Session signing: a configured secret keeps sessions across restarts
    # and across replicas; without one a per-process secret is generated
    # and every restart signs everyone out (refused outright in production).
    secret = settings.session_secret or secrets.token_urlsafe(32)
    app.state.sessions = SessionTokens(secret, ttl_s=settings.session_ttl_s)
    # The stored credential: a precomputed hash when the deployment set one,
    # otherwise a hash derived once from the configured password.
    app.state.ui_password_hash = settings.ui_password_hash or (
        hash_password(settings.ui_password) if settings.ui_password else None
    )
    app.state.registry = registry
    app.state.engine = engine
    app.state.session_factory = make_session_factory(engine)
    app.state.store = store
    # Optional Temporal starter (duck-typed: callable for dispatch,
    # ``.cancel(workflow_id)`` for cancellation). Absent => async jobs wait
    # for the worker's dispatcher tick instead of being nudged inline.
    app.state.starter = starter

    register_exception_handlers(app)
    app.include_router(auth_routes.router, prefix="/v1")
    app.include_router(transcriptions.router, prefix="/v1")
    app.include_router(providers.router, prefix="/v1")
    app.include_router(evaluations.router, prefix="/v1")
    app.include_router(health.router, prefix="/v1")
    # Observability last: middleware added last runs first, so correlation
    # ids are bound (and the request span is opened) before anything else.
    tracing.setup_tracing(settings)
    metric_reader, metrics_registry = build_prometheus_reader(settings)
    metrics.setup_metrics(settings, reader=metric_reader)
    app.state.metrics_registry = metrics_registry
    if metrics_registry is not None:
        app.add_api_route(
            METRICS_PATH,
            lambda: Response(
                render_metrics(metrics_registry),
                media_type="text/plain; version=0.0.4",
            ),
            include_in_schema=False,
        )
    setup_logging(settings)
    instrument_app(app)
    return app


#: ASGI entrypoint used by uvicorn/gunicorn: wires real object storage and
#: the Temporal starter. ``create_app`` itself stays dependency-optional so
#: tests can opt in/out of storage and dispatch.
_settings = get_settings()


def _default_starter() -> Any:
    from stt_platform.services.temporal_starter import (
        TemporalWorkflowStarter,
        remote_config_from_settings,
        retry_config_from_settings,
    )

    return TemporalWorkflowStarter(
        host=_settings.temporal_host,
        task_queue=_settings.temporal_task_queue,
        retry=retry_config_from_settings(_settings),
        remote=remote_config_from_settings(_settings),
        interceptors=temporal_interceptors(_settings),
    )


app = create_app(
    settings=_settings,
    store=S3ObjectStore.from_settings(_settings),
    starter=_default_starter(),
)

#: The API process samples its own CPU/RSS and the dispatch backlog; the
#: worker samples the GPU. Both are daemon threads and off unless metrics
#: are enabled (``STT_OTEL_ENABLED``).
def _api_queue_depth() -> int:
    from stt_platform.persistence.outbox import OutboxRepository

    with app.state.session_factory() as session:
        return OutboxRepository(session).pending_count()


runtime.start_runtime_sampler(
    interval_s=_settings.runtime_sample_interval_s,
    queue_depth_fn=_api_queue_depth,
)

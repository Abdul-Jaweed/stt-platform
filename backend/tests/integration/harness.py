"""Shared, non-collected helpers for Phase 2 integration tests."""
from __future__ import annotations

import tempfile
import uuid
from pathlib import Path

from stt_platform.core.config import Settings
from stt_platform.domain.status import JobStatus
from stt_platform.persistence.repositories import JobRepository
from stt_platform.providers.base import ProviderProtocol
from stt_platform.providers.registry import ProviderRegistry
from stt_platform.schemas.jobs import JobRecord
from stt_platform.storage.keys import original_key
from stt_platform.storage.s3 import S3ObjectStore
from stt_platform.workers.activities import ActivityDeps
from tests.audio_fixtures import make_wav
from tests.rustfs_container import ACCESS_KEY, SECRET_KEY


def build_store(rustfs) -> S3ObjectStore:
    store = S3ObjectStore(
        endpoint_url=rustfs.s3_endpoint(),
        access_key=ACCESS_KEY,
        secret_key=SECRET_KEY,
        region="us-east-1",
        bucket_audio="stt-audio",
        bucket_artifacts="stt-artifacts",
    )
    store.ensure_buckets()
    return store


def make_settings(tmp_path: Path) -> Settings:
    tmp_dir = tmp_path / "tmp"
    audio_dir = tmp_path / "audio"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    audio_dir.mkdir(parents=True, exist_ok=True)
    return Settings(temp_dir=tmp_dir, audio_store_dir=audio_dir)


def build_deps(
    session_factory,
    provider: ProviderProtocol,
    store: S3ObjectStore | None,
    settings: Settings,
) -> ActivityDeps:
    registry = ProviderRegistry()
    registry.register(provider)
    return ActivityDeps(
        settings=settings,
        session_factory=session_factory,
        registry=registry,
        store=store,
    )


def seed_job(
    session_factory,
    store: S3ObjectStore | None,
    *,
    scope: str = "local",
    status: JobStatus = JobStatus.QUEUED,
    corrupt: bool = False,
) -> str:
    """Create a job whose original audio lives in object storage."""
    job_id = str(uuid.uuid4())
    key = original_key(scope, job_id, ".wav")
    if store is not None:
        if corrupt:
            store.put_bytes(store.bucket_audio, key, b"this is not audio at all")
        else:
            with tempfile.TemporaryDirectory() as td:
                wav = make_wav(Path(td) / "tone.wav", seconds=1.0)
                store.put_file(store.bucket_audio, key, wav)
    with session_factory() as session:
        repo = JobRepository(session)
        repo.create(
            JobRecord(
                id=job_id, scope=scope, status=status, requested_provider="fake"
            ),
            original_object_key=key,
        )
    return job_id

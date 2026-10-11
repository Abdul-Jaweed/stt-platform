"""Evaluations in PostgreSQL, artifacts in object storage.

PostgreSQL keeps the numbers (queryable, cheap); the full result — including
transcripts — goes to object storage as an artifact, exactly like every
other transcript in this platform.
"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from stt_platform.evaluation.datasets import Clip, Dataset
from stt_platform.evaluation.runner import ClipOutcome, run_evaluation
from stt_platform.persistence.evaluations import (
    EvaluationNotFound,
    EvaluationRepository,
)
from stt_platform.persistence.models import Base
from stt_platform.storage.keys import evaluation_artifact_key
from tests.integration.harness import build_store
from tests.rustfs_container import RustFSContainer


@pytest.fixture(scope="module")
def rustfs():
    with RustFSContainer() as container:
        yield container


@pytest.fixture(scope="module")
def store(rustfs):
    return build_store(rustfs)


@pytest.fixture(scope="session")
def pg_engine(pg_url):
    engine = create_engine(pg_url, future=True)
    yield engine
    engine.dispose()


@pytest.fixture()
def session_factory(pg_engine):
    Base.metadata.drop_all(pg_engine)
    Base.metadata.create_all(pg_engine)
    return sessionmaker(bind=pg_engine, future=True)


def _result(*, provider: str = "fake", wer_text: str = "hello world"):
    def transcribe(clip: Clip) -> ClipOutcome:
        return ClipOutcome(
            text=wer_text,
            segments=(
                SimpleNamespace(
                    start=0.0, end=4.0, words=[SimpleNamespace(start=0.0, end=4.0)]
                ),
            ),
            granularity="word",
            timings={
                "queue_wait_s": 0.3,
                "processing_s": 1.2,
                "end_to_end_s": 1.6,
                "real_time_factor": 0.3,
            },
            attempts=1,
            retries=0,
            provider=provider,
            model="fake-v1",
        )

    clip = Clip(
        clip_id="clip-a",
        audio=Path("/tmp/clip-a.wav"),
        reference_text="hello world",
        duration_s=4.0,
        language="en",
        domain="technical",
        accent="synthetic-us",
        noise="none",
        reference_source="synthetic-tts",
        reference_segments=(SimpleNamespace(start=0.0, end=4.0, text="hello world"),),
        sha256="abc123",
    )
    dataset = Dataset(
        dataset_id="stt-eval-core",
        version="1.0.0",
        language="en",
        audio_root=Path("/tmp"),
        clips=(clip,),
        missing_audio=(),
        path=Path("/tmp/manifest.json"),
    )
    return run_evaluation(
        dataset=dataset,
        transcribe=transcribe,
        provider=provider,
        model="fake-v1",
        decoding={"language": "en"},
    )


def test_evaluation_round_trips_through_postgres(session_factory):
    with session_factory() as session:
        repository = EvaluationRepository(session)
        record = repository.save(_result())
        session.commit()

        loaded = repository.get(record.evaluation_id)

    assert loaded.dataset_id == "stt-eval-core"
    assert loaded.dataset_version == "1.0.0"
    assert loaded.provider == "fake"
    assert loaded.aggregate["wer_micro"] == 0.0
    assert loaded.aggregate["clips_scored"] == 1
    assert loaded.by_group["domain"]["technical"]["clips"] == 1
    assert loaded.decoding == {"language": "en"}
    assert loaded.normalization["name"] == "stt-normalize-v1"
    assert loaded.insufficient_evidence is True
    assert loaded.duration_s >= 0
    assert len(loaded.clips) == 1
    clip = loaded.clips[0]
    assert clip.clip_id == "clip-a"
    assert clip.status == "scored"
    assert clip.wer == 0.0
    assert clip.audio_sha256 == "abc123"
    assert clip.timings["processing_s"] == 1.2
    assert clip.timestamps is not None
    assert clip.timestamps["word_availability"] == 1.0
    # Transcript text stays out of PostgreSQL: it lives in the artifact.
    assert not hasattr(clip, "transcript_text")


def test_unknown_evaluation_is_not_found(session_factory):
    with session_factory() as session, pytest.raises(EvaluationNotFound):
        EvaluationRepository(session).get("nope")


def test_list_returns_newest_first_with_comparison_rows(session_factory):
    with session_factory() as session:
        repository = EvaluationRepository(session)
        first = repository.save(_result(provider="fake"))
        second = repository.save(_result(provider="other", wer_text="hello there"))
        session.commit()

        rows = repository.list(limit=10)
        comparison = repository.comparison()

    assert next(row.evaluation_id for row in rows) == second.evaluation_id
    assert {row.evaluation_id for row in rows} == {
        first.evaluation_id,
        second.evaluation_id,
    }
    by_provider = {row.provider: row for row in comparison}
    assert by_provider["fake"].wer_micro == 0.0
    assert by_provider["other"].wer_micro == pytest.approx(0.5)
    assert by_provider["fake"].clips_scored == 1
    assert by_provider["fake"].evaluation_id == first.evaluation_id


def test_comparison_marks_single_clip_evaluations_as_insufficient(session_factory):
    with session_factory() as session:
        repository = EvaluationRepository(session)
        repository.save(_result())
        session.commit()
        comparison = repository.comparison()

    assert comparison[0].insufficient_evidence is True
    assert comparison[0].clips_scored == 1


def test_result_artifact_is_stored_and_keyed_opaque(session_factory, store):
    with session_factory() as session:
        repository = EvaluationRepository(session)
        record = repository.save(_result(), store=store)
        session.commit()

        payload = json.loads(
            store.get_bytes(
                store.bucket_artifacts,
                evaluation_artifact_key(record.evaluation_id),
            )
        )

    assert record.artifact_key == evaluation_artifact_key(record.evaluation_id)
    assert payload["dataset"]["dataset_id"] == "stt-eval-core"
    assert payload["clips"][0]["transcript_text"] == "hello world"
    # Opaque key: the dataset name and audio paths never leak into it.
    assert "stt-eval-core" not in record.artifact_key
    assert "clip-a" not in record.artifact_key
    assert record.artifact_key.startswith("artifacts/")


def test_deleting_an_evaluation_removes_its_clip_rows(session_factory):
    from sqlalchemy import func, select

    from stt_platform.persistence.models import EvaluationClipRow

    with session_factory() as session:
        repository = EvaluationRepository(session)
        record = repository.save(_result())
        session.commit()
        repository.delete(record.evaluation_id)
        session.commit()
        remaining = session.execute(
            select(func.count()).select_from(EvaluationClipRow)
        ).scalar_one()

    assert remaining == 0

"""Evaluation endpoints: run an evaluation, read it back, compare providers.

A run is synchronous on purpose: the shipped corpus is minutes of audio at
RTF ~0.1, so a bounded run finishes inside one request (the cap is
``STT_EVALUATION_MAX_CLIPS``, default 25). Anything larger belongs in a
workflow, not a request — and the API says so instead of hanging.

The provider that *actually* ran is recorded per clip, from the job record;
``provider`` in the request only selects the adapter.
"""
from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends, Request

from stt_platform.api.deps import get_service, get_session
from stt_platform.api.security import get_scope
from stt_platform.domain.errors import (
    DatasetNotFound,
    DatasetNotReady,
    EvaluationArtifactMissing,
    EvaluationTooLarge,
    ProviderNotFound,
    StorageNotConfigured,
)
from stt_platform.evaluation.datasets import (
    available_datasets,
    dataset_path,
    load_dataset,
)
from stt_platform.evaluation.metrics import METRIC_SPECS
from stt_platform.evaluation.mlflow_logging import log_evaluation
from stt_platform.evaluation.platform import PlatformTranscriber
from stt_platform.evaluation.runner import RUNNER_METRIC_SPECS, run_evaluation
from stt_platform.persistence.evaluations import EvaluationRepository
from stt_platform.schemas.evaluations import (
    EvaluationDatasetInfo,
    EvaluationDatasetList,
    EvaluationList,
    EvaluationRecord,
    EvaluationRequest,
    MetricExplanation,
    MetricExplanationList,
)
from stt_platform.services.jobs import JobService

router = APIRouter(
    prefix="/evaluations", tags=["evaluations"], dependencies=[Depends(get_scope)]
)


def _resolve_dataset(payload: EvaluationRequest, settings):
    directory = settings.evaluation_datasets_dir
    path = dataset_path(payload.dataset_id, directory=directory)
    if not path.is_file():
        raise DatasetNotFound(payload.dataset_id, available_datasets(directory=directory))
    dataset = load_dataset(path, audio_root=settings.evaluation_audio_root)
    if dataset.missing_scored_audio():
        raise DatasetNotReady(list(dataset.missing_scored_audio()))
    scored = len(dataset.scored_clips())
    if scored > settings.evaluation_max_clips:
        raise EvaluationTooLarge(scored, settings.evaluation_max_clips)
    return dataset


@router.post("", status_code=201, response_model=EvaluationRecord)
def create_evaluation(
    payload: EvaluationRequest,
    request: Request,
    service: JobService = Depends(get_service),
    session=Depends(get_session),
) -> EvaluationRecord:
    settings = request.app.state.settings
    registry = request.app.state.registry
    dataset = _resolve_dataset(payload, settings)

    try:
        provider = registry.get(payload.provider)
    except Exception as exc:  # ProviderNotFound from the registry
        raise ProviderNotFound(payload.provider or "default") from exc

    options = dict(payload.options)
    if payload.language:
        options["language"] = payload.language
    decoding = {"language": payload.language or "auto", **options}

    result = run_evaluation(
        dataset=dataset,
        transcribe=PlatformTranscriber(
            service=service, provider_id=provider.id, options=options
        ),
        provider=provider.id,
        decoding=decoding,
    )

    mlflow_outcome = (
        log_evaluation(
            result,
            tracking_uri=settings.mlflow_tracking_uri,
            experiment_name=settings.mlflow_experiment,
            timeout_s=settings.mlflow_timeout_s,
        )
        if settings.mlflow_enabled
        else {"status": "disabled"}
    )

    repository = EvaluationRepository(session)
    record = repository.save(
        result, store=getattr(request.app.state, "store", None), mlflow=mlflow_outcome
    )
    session.commit()
    return record


@router.get("/{evaluation_id}/artifact")
def get_evaluation_artifact(
    evaluation_id: str, request: Request, session=Depends(get_session)
) -> dict[str, Any]:
    """The stored artifact: per-clip references, transcripts and alignments.

    The record is the index — numbers, comparable across runs; the words
    live in the artifact, exactly as transcripts live in object storage
    rather than in the jobs table. This is how the console renders the diff
    behind a WER, and it answers honestly when there is nothing to serve: a
    run stored without a store says so instead of inventing a document.
    """
    record = EvaluationRepository(session).get(evaluation_id)
    store = getattr(request.app.state, "store", None)
    if store is None:
        raise StorageNotConfigured("this deployment has no object storage configured")
    if not record.artifact_key:
        raise EvaluationArtifactMissing(evaluation_id)
    try:
        data = store.get_bytes(store.bucket_artifacts, record.artifact_key)
    except Exception as exc:  # missing object, unreachable store
        raise EvaluationArtifactMissing(evaluation_id) from exc
    return json.loads(data)


@router.get("", response_model=EvaluationList)
def list_evaluations(request: Request, session=Depends(get_session)) -> EvaluationList:
    repository = EvaluationRepository(session)
    rows = repository.comparison()
    return EvaluationList(evaluations=rows, count=len(rows))


@router.get("/metrics", response_model=MetricExplanationList)
def list_metric_explanations() -> MetricExplanationList:
    """Every metric the platform reports, with the callable behind it.

    Served from the metric specs themselves, so this page cannot describe a
    formula the code does not implement. Accuracy rates come from
    ``evaluation.metrics``; the run-level numbers (failure rate, speed,
    timestamp coverage) from ``evaluation.runner``.
    """
    specs = list(METRIC_SPECS.values()) + list(RUNNER_METRIC_SPECS.values())
    return MetricExplanationList(
        metrics=[MetricExplanation(**spec.describe()) for spec in specs]
    )


@router.get("/datasets", response_model=EvaluationDatasetList)
def list_datasets(request: Request) -> EvaluationDatasetList:
    """Every dataset this deployment can run, with its readiness.

    The corpus audio is generated (``scripts/make_eval_corpus.py``) and never
    committed, so "the manifest exists" and "a run can start" are different
    statements: each dataset is listed with its scored-clip count and whether
    its scored audio is present. A manifest that cannot be loaded is still
    listed, with ``load_error`` set — disappearing silently would hide it.
    """
    settings = request.app.state.settings
    directory = settings.evaluation_datasets_dir
    datasets: list[EvaluationDatasetInfo] = []
    for dataset_id in available_datasets(directory=directory):
        path = dataset_path(dataset_id, directory=directory)
        try:
            dataset = load_dataset(path, audio_root=settings.evaluation_audio_root)
            raw = json.loads(path.read_text(encoding="utf-8"))
            description = raw.get("description")
            missing = dataset.missing_scored_audio()
        except Exception as exc:  # a broken manifest is reported, not hidden
            datasets.append(
                EvaluationDatasetInfo(
                    dataset_id=dataset_id,
                    version="unknown",
                    language="unknown",
                    clips_total=0,
                    clips_scored=0,
                    clips_unscored=0,
                    audio_ready=False,
                    clips_missing_audio=0,
                    load_error=f"{type(exc).__name__}: {exc}",
                )
            )
            continue
        scored = len(dataset.scored_clips())
        datasets.append(
            EvaluationDatasetInfo(
                dataset_id=dataset.dataset_id,
                version=dataset.version,
                language=dataset.language,
                description=description if isinstance(description, str) else None,
                clips_total=len(dataset.clips),
                clips_scored=scored,
                clips_unscored=len(dataset.clips) - scored,
                audio_ready=not missing,
                clips_missing_audio=len(missing),
            )
        )
    return EvaluationDatasetList(datasets=datasets)


@router.get("/{evaluation_id}", response_model=EvaluationRecord)
def get_evaluation(
    evaluation_id: str, request: Request, session=Depends(get_session)
) -> EvaluationRecord:
    return EvaluationRepository(session).get(evaluation_id)

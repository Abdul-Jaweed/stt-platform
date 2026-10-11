"""Evaluation endpoints: run an evaluation, read it back, compare providers.

A run is synchronous on purpose: the shipped corpus is minutes of audio at
RTF ~0.1, so a bounded run finishes inside one request (the cap is
``STT_EVALUATION_MAX_CLIPS``, default 25). Anything larger belongs in a
workflow, not a request — and the API says so instead of hanging.

The provider that *actually* ran is recorded per clip, from the job record;
``provider`` in the request only selects the adapter.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from stt_platform.api.deps import get_service, get_session
from stt_platform.api.security import get_scope
from stt_platform.domain.errors import (
    DatasetNotFound,
    DatasetNotReady,
    EvaluationTooLarge,
    ProviderNotFound,
)
from stt_platform.evaluation.datasets import (
    available_datasets,
    dataset_path,
    load_dataset,
)
from stt_platform.evaluation.mlflow_logging import log_evaluation
from stt_platform.evaluation.platform import PlatformTranscriber
from stt_platform.evaluation.runner import run_evaluation
from stt_platform.persistence.evaluations import EvaluationRepository
from stt_platform.schemas.evaluations import (
    EvaluationList,
    EvaluationRecord,
    EvaluationRequest,
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


@router.get("", response_model=EvaluationList)
def list_evaluations(request: Request, session=Depends(get_session)) -> EvaluationList:
    repository = EvaluationRepository(session)
    rows = repository.comparison()
    return EvaluationList(evaluations=rows, count=len(rows))


@router.get("/{evaluation_id}", response_model=EvaluationRecord)
def get_evaluation(
    evaluation_id: str, request: Request, session=Depends(get_session)
) -> EvaluationRecord:
    return EvaluationRepository(session).get(evaluation_id)

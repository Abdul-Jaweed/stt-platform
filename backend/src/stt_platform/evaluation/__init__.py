"""Evaluation: reference datasets, accuracy metrics, MLflow tracking.

Production operations are observed with OpenTelemetry (Phase 5); *quality*
is measured here, reproducibly, against a versioned reference dataset.
"""
from __future__ import annotations

from stt_platform.evaluation.datasets import (
    Clip,
    Dataset,
    DatasetError,
    load_dataset,
)

__all__ = ["Clip", "Dataset", "DatasetError", "load_dataset"]

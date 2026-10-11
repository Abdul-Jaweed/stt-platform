"""SQLAlchemy ORM models: jobs, their state-transition trail, and the outbox."""
from datetime import UTC, datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


def _utcnow() -> datetime:
    return datetime.now(UTC)


class JobRow(Base):
    __tablename__ = "jobs"
    __table_args__ = (
        UniqueConstraint(
            "scope", "idempotency_key", name="uq_jobs_scope_idempotency_key"
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    scope: Mapped[str] = mapped_column(String(64), nullable=False, default="local")
    status: Mapped[str] = mapped_column(String(24), nullable=False)

    requested_provider: Mapped[str | None] = mapped_column(String(64))
    actual_provider: Mapped[str | None] = mapped_column(String(64))
    model: Mapped[str | None] = mapped_column(String(128))
    model_version: Mapped[str | None] = mapped_column(String(64))
    language_requested: Mapped[str | None] = mapped_column(String(16))
    language_detected: Mapped[str | None] = mapped_column(String(16))

    audio_format: Mapped[str | None] = mapped_column(String(64))
    audio_codec: Mapped[str | None] = mapped_column(String(64))
    audio_sample_rate: Mapped[int | None] = mapped_column(Integer)
    audio_channels: Mapped[int | None] = mapped_column(Integer)
    audio_duration_s: Mapped[float | None] = mapped_column(Float)
    audio_size_bytes: Mapped[int | None] = mapped_column(Integer)
    audio_path: Mapped[str | None] = mapped_column(String(512))
    original_object_key: Mapped[str | None] = mapped_column(String(512))

    idempotency_key: Mapped[str | None] = mapped_column(String(255))
    request_fingerprint: Mapped[str | None] = mapped_column(String(64))
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    failure: Mapped[dict | None] = mapped_column(JSONB)
    options: Mapped[dict] = mapped_column(JSONB, default=dict)
    transcript: Mapped[dict | None] = mapped_column(JSONB)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    transitions = relationship(
        "JobTransitionRow",
        back_populates="job",
        order_by="JobTransitionRow.id",
        cascade="all, delete-orphan",
    )


class JobTransitionRow(Base):
    __tablename__ = "job_transitions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[str] = mapped_column(
        ForeignKey("jobs.id", ondelete="CASCADE"), index=True
    )
    from_status: Mapped[str] = mapped_column(String(24), nullable=False)
    to_status: Mapped[str] = mapped_column(String(24), nullable=False)
    detail: Mapped[dict | None] = mapped_column(JSONB)
    at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )

    job = relationship("JobRow", back_populates="transitions")


class OutboxRow(Base):
    """Transactional outbox: a committed row guarantees eventual dispatch."""

    __tablename__ = "outbox"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[str] = mapped_column(
        ForeignKey("jobs.id", ondelete="CASCADE"), index=True
    )
    topic: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )
    dispatched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_error: Mapped[str | None] = mapped_column(String(500))


class ProviderAttemptRow(Base):
    """One provider-side submission attempt (audit + reconciliation).

    A ``SUBMITTING`` row is written *before* the provider call so a retry
    of the same attempt can never blindly resubmit; ``SUBMITTED`` carries
    the provider job reference the workflow polls.
    """

    __tablename__ = "provider_attempts"
    __table_args__ = (
        UniqueConstraint("job_id", "attempt", name="uq_provider_attempts_job_attempt"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[str] = mapped_column(
        ForeignKey("jobs.id", ondelete="CASCADE"), index=True
    )
    scope: Mapped[str] = mapped_column(String(64), nullable=False, default="local")
    attempt: Mapped[int] = mapped_column(Integer, nullable=False)
    provider_id: Mapped[str] = mapped_column(String(64), nullable=False)
    provider_job_id: Mapped[str | None] = mapped_column(String(255))
    state: Mapped[str] = mapped_column(String(24), nullable=False)
    detail: Mapped[dict | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False
    )


class EvaluationRow(Base):
    """One evaluation run: dataset x provider x configuration.

    The numbers live here; the full result (transcripts included) is an
    artifact in object storage, exactly like every other transcript.
    """

    __tablename__ = "evaluations"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    dataset_id: Mapped[str] = mapped_column(String(128), index=True)
    dataset_version: Mapped[str] = mapped_column(String(64))
    provider: Mapped[str] = mapped_column(String(64), index=True)
    model: Mapped[str | None] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(24), default="COMPLETED")
    decoding: Mapped[dict] = mapped_column(JSONB, default=dict)
    normalization: Mapped[dict] = mapped_column(JSONB, default=dict)
    aggregate: Mapped[dict] = mapped_column(JSONB, default=dict)
    by_group: Mapped[dict] = mapped_column(JSONB, default=dict)
    insufficient_evidence: Mapped[bool] = mapped_column(Boolean, default=False)
    clips_total: Mapped[int] = mapped_column(Integer, default=0)
    clips_scored: Mapped[int] = mapped_column(Integer, default=0)
    wer_micro: Mapped[float | None] = mapped_column(Float)
    cer_micro: Mapped[float | None] = mapped_column(Float)
    mlflow: Mapped[dict | None] = mapped_column(JSONB)
    artifact_key: Mapped[str | None] = mapped_column(String(256))
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    duration_s: Mapped[float] = mapped_column(Float, default=0.0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )

    clips: Mapped[list["EvaluationClipRow"]] = relationship(
        back_populates="evaluation",
        cascade="all, delete-orphan",
        order_by="EvaluationClipRow.clip_id",
    )


class EvaluationClipRow(Base):
    """Per-clip metrics. No transcript text: that is the artifact's job."""

    __tablename__ = "evaluation_clips"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    evaluation_id: Mapped[str] = mapped_column(
        ForeignKey("evaluations.id", ondelete="CASCADE"), index=True
    )
    clip_id: Mapped[str] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(24))
    provider: Mapped[str] = mapped_column(String(64))
    model: Mapped[str | None] = mapped_column(String(128))
    audio_sha256: Mapped[str | None] = mapped_column(String(64))
    wer: Mapped[float | None] = mapped_column(Float)
    cer: Mapped[float | None] = mapped_column(Float)
    word_errors: Mapped[dict | None] = mapped_column(JSONB)
    char_errors: Mapped[dict | None] = mapped_column(JSONB)
    timings: Mapped[dict] = mapped_column(JSONB, default=dict)
    timestamps: Mapped[dict | None] = mapped_column(JSONB)
    error: Mapped[str | None] = mapped_column(String(512))

    evaluation: Mapped[EvaluationRow] = relationship(back_populates="clips")

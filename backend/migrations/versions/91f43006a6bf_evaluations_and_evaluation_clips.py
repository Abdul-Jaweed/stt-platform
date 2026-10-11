"""evaluations and evaluation_clips: accuracy records in PostgreSQL

Transcript text is deliberately absent: it lives in the result artifact
under ``artifacts/evaluation/<id>/result.json``.
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "91f43006a6bf"
down_revision = "7d2e9c4b81f5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "evaluations",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("dataset_id", sa.String(length=128), nullable=False),
        sa.Column("dataset_version", sa.String(length=64), nullable=False),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("model", sa.String(length=128), nullable=True),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("decoding", postgresql.JSONB(), nullable=False),
        sa.Column("normalization", postgresql.JSONB(), nullable=False),
        sa.Column("aggregate", postgresql.JSONB(), nullable=False),
        sa.Column("by_group", postgresql.JSONB(), nullable=False),
        sa.Column("insufficient_evidence", sa.Boolean(), nullable=False),
        sa.Column("clips_total", sa.Integer(), nullable=False),
        sa.Column("clips_scored", sa.Integer(), nullable=False),
        sa.Column("wer_micro", sa.Float(), nullable=True),
        sa.Column("cer_micro", sa.Float(), nullable=True),
        sa.Column("mlflow", postgresql.JSONB(), nullable=True),
        sa.Column("artifact_key", sa.String(length=256), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_s", sa.Float(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_evaluations_dataset_id", "evaluations", ["dataset_id"])
    op.create_index("ix_evaluations_provider", "evaluations", ["provider"])

    op.create_table(
        "evaluation_clips",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "evaluation_id",
            sa.String(length=64),
            sa.ForeignKey("evaluations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("clip_id", sa.String(length=128), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("model", sa.String(length=128), nullable=True),
        sa.Column("audio_sha256", sa.String(length=64), nullable=True),
        sa.Column("wer", sa.Float(), nullable=True),
        sa.Column("cer", sa.Float(), nullable=True),
        sa.Column("word_errors", postgresql.JSONB(), nullable=True),
        sa.Column("char_errors", postgresql.JSONB(), nullable=True),
        sa.Column("timings", postgresql.JSONB(), nullable=False),
        sa.Column("timestamps", postgresql.JSONB(), nullable=True),
        sa.Column("error", sa.String(length=512), nullable=True),
    )
    op.create_index(
        "ix_evaluation_clips_evaluation_id", "evaluation_clips", ["evaluation_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_evaluation_clips_evaluation_id", table_name="evaluation_clips")
    op.drop_table("evaluation_clips")
    op.drop_index("ix_evaluations_provider", table_name="evaluations")
    op.drop_index("ix_evaluations_dataset_id", table_name="evaluations")
    op.drop_table("evaluations")

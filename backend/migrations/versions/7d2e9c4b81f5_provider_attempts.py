"""provider_attempts: provider-side submission audit and reconciliation

Revision ID: 7d2e9c4b81f5
Revises: 1aa61d61c6a4
Create Date: 2026-10-11
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "7d2e9c4b81f5"
down_revision = "1aa61d61c6a4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "provider_attempts",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "job_id",
            sa.String(length=36),
            sa.ForeignKey("jobs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "scope", sa.String(length=64), nullable=False, server_default="local"
        ),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("provider_id", sa.String(length=64), nullable=False),
        sa.Column("provider_job_id", sa.String(length=255), nullable=True),
        sa.Column("state", sa.String(length=24), nullable=False),
        sa.Column("detail", postgresql.JSONB(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "job_id", "attempt", name="uq_provider_attempts_job_attempt"
        ),
    )
    op.create_index(
        "ix_provider_attempts_job_id", "provider_attempts", ["job_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_provider_attempts_job_id", table_name="provider_attempts")
    op.drop_table("provider_attempts")

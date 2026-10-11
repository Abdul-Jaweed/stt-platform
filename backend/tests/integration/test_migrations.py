"""Alembic migrations apply and roll back against a real PostgreSQL."""
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text

from stt_platform.persistence.models import Base  # noqa: F401  (models import cleanly)

BACKEND_DIR = Path(__file__).resolve().parents[2]


def test_upgrade_and_downgrade_roundtrip(pg_url):
    engine = create_engine(pg_url)
    # Earlier integration files can leave tables behind in this session's
    # database; migrations must be exercised from a clean slate.
    with engine.begin() as conn:
        conn.execute(
            text(
                "DROP TABLE IF EXISTS job_transitions, jobs, alembic_version CASCADE"
            )
        )

    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", pg_url)

    command.upgrade(cfg, "head")
    tables = set(inspect(engine).get_table_names())
    assert {"jobs", "job_transitions"} <= tables

    command.downgrade(cfg, "base")
    tables = set(inspect(engine).get_table_names())
    assert "jobs" not in tables
    assert "job_transitions" not in tables
    engine.dispose()

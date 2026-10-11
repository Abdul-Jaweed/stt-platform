#!/usr/bin/env python
"""Purge jobs past the retention window (objects first, then rows).

    python scripts/purge_expired.py --dry-run          # what would go?
    python scripts/purge_expired.py                    # settings.retention_days
    python scripts/purge_expired.py --older-than-days 7

Retention is *off* by default (``STT_RETENTION_DAYS=0``): a zero window means
"keep everything", and this tool refuses to guess — pass ``--older-than-days``
explicitly when the setting is 0.

Only terminal jobs (SUCCEEDED / FAILED / CANCELLED) are candidates; a job that
is still running is never deleted, whatever its age.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from stt_platform.core.config import get_settings
from stt_platform.persistence.db import make_engine, make_session_factory
from stt_platform.services.retention import purge_expired
from stt_platform.storage.s3 import S3ObjectStore


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    settings = get_settings()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--older-than-days",
        type=int,
        default=None,
        help="retention window; defaults to STT_RETENTION_DAYS "
        f"(currently {settings.retention_days})",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="list the candidates and delete nothing",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    settings = get_settings()

    window = args.older_than_days
    if window is None:
        window = settings.retention_days
    if window <= 0:
        print(
            json.dumps(
                {
                    "status": "refused",
                    "reason": "retention is disabled (STT_RETENTION_DAYS=0); "
                    "pass --older-than-days to purge explicitly",
                }
            )
        )
        return 2

    engine = make_engine(settings.database_url)
    session_factory = make_session_factory(engine)
    store = S3ObjectStore.from_settings(settings)

    with session_factory() as session:
        report = purge_expired(
            session, store, older_than_days=window, dry_run=args.dry_run
        )
        if not args.dry_run:
            session.commit()

    payload = report.as_dict()
    payload["older_than_days"] = window
    print(json.dumps(payload, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python
"""Write ``docs/openapi.json`` from the application, deterministically.

Run from ``backend/`` so the package resolves::

    cd backend && uv run python ../scripts/export_openapi.py

The committed file is compared against the live schema by
``tests/integration/test_openapi.py::test_the_committed_spec_matches_the_application``
— regenerate in the same commit as any route or schema change, or the
suite (rightly) fails.
"""
from __future__ import annotations

import json
from pathlib import Path

from stt_platform.core.config import Settings
from stt_platform.main import create_app
from stt_platform.providers.faster_whisper import FasterWhisperProvider
from stt_platform.providers.registry import ProviderRegistry

REPO_ROOT = Path(__file__).resolve().parents[1]
DEST = REPO_ROOT / "docs" / "openapi.json"


def build_schema() -> dict:
    # A provider is registered so the listing endpoint's examples are real;
    # nothing here touches a database, a model or a network.
    registry = ProviderRegistry()
    registry.register(FasterWhisperProvider(settings=Settings(_env_file=None)))
    settings = Settings(
        _env_file=None,
        repo_root=REPO_ROOT,
        temp_dir=REPO_ROOT / ".data" / "tmp",
        audio_store_dir=REPO_ROOT / ".data" / "uploads",
        database_url="postgresql+psycopg://u:p@127.0.0.1:1/db",
    )
    return create_app(settings=settings, registry=registry).openapi()


def main() -> int:
    schema = build_schema()
    DEST.write_text(
        json.dumps(schema, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(f"wrote {DEST} ({len(schema['paths'])} paths, title={schema['info']['title']!r})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

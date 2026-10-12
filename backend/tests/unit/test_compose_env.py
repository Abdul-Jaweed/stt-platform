"""Deployment wiring: what docker-compose.yml must forward into the containers.

A setting that is documented (``.env.example``, ``docs/deployment.md``) but
never forwarded by compose is invisible to the containers: adding it to ``.env``
and restarting changes nothing. That has already bitten twice - the sync
duration knob, and the managed-provider keys - so it is asserted here instead
of being remembered.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
COMPOSE = REPO_ROOT / "docker-compose.yml"
ENV_EXAMPLE = REPO_ROOT / ".env.example"

# Documented, optional, and only meaningful to the app process: each must reach
# the API and worker containers from the environment.
FORWARDED = [
    "STT_DEEPGRAM_API_KEY",
    "STT_ELEVENLABS_API_KEY",
    "STT_SARVAM_API_KEY",
    "STT_AUTH_ENABLED",
    "STT_API_KEYS",
    # The live stream's own knobs: on by default, so a deployment that wants
    # different limits must be able to say so from .env.
    "STT_STREAM_MAX_DURATION_S",
    "STT_STREAM_IDLE_TIMEOUT_S",
    "STT_STREAM_MAX_SESSIONS",
    "STT_STREAM_MAX_FRAME_BYTES",
    # Observability is on by default now: the off switch and the exporter
    # knobs must be settable from .env, or a deployment cannot aim traces.
    "STT_OTEL_ENABLED",
    "STT_OTEL_EXPORTER",
    "STT_OTEL_ENDPOINT",
    # The login page's demo panel: on by default for the local stack, and
    # off for anything others can reach - so a deployment has to be able to
    # say so from .env, not by rebuilding the frontend.
    "STT_AUTH_DEMO_MODE",
]


@pytest.fixture(scope="module")
def compose_text() -> str:
    return COMPOSE.read_text(encoding="utf-8")


@pytest.mark.parametrize("name", FORWARDED)
def test_compose_forwards_the_variable(compose_text: str, name: str) -> None:
    pattern = re.compile(rf"^\s*{name}:\s*\$\{{{name}(:-[^}}]*)?\}}", re.MULTILINE)
    assert pattern.search(compose_text), (
        f"docker-compose.yml does not forward {name}: setting it in .env "
        "would never reach the containers"
    )


@pytest.mark.parametrize("name", FORWARDED)
def test_the_variable_is_documented_in_env_example(name: str) -> None:
    assert name in ENV_EXAMPLE.read_text(encoding="utf-8")

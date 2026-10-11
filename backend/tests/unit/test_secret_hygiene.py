"""Secret hygiene: what must never be committed, asserted against the index.

A public repository makes these checks load-bearing, so they run as tests
rather than as a habit: no credential-shaped strings in tracked files, no
``.env`` in the index, no audio or model binaries (ADR-007), and no live
values in ``.env.example``.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]

# Credential-shaped strings. Deliberately specific: this is a tripwire for
# real keys, not a lint that fires on prose.
SECRET_PATTERNS: dict[str, re.Pattern[str]] = {
    "aws_access_key": re.compile(r"AKIA[0-9A-Z]{16}"),
    "openai_style_key": re.compile(r"\bsk-[A-Za-z0-9]{20,}\b"),
    "google_api_key": re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b"),
    "huggingface_token": re.compile(r"\bhf_[A-Za-z0-9]{30,}\b"),
    "private_key_block": re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
}

BINARY_SUFFIXES = {
    ".wav", ".mp3", ".m4a", ".flac", ".ogg", ".webm", ".mp4",
    ".pt", ".bin", ".onnx", ".safetensors", ".ckpt",
}

TEXT_SUFFIXES = {
    "", ".py", ".md", ".txt", ".toml", ".cfg", ".ini", ".yaml", ".yml",
    ".json", ".js", ".jsx", ".ts", ".tsx", ".html", ".css", ".env", ".example",
    ".sh", ".ps1", ".lock", ".gitignore", ".gitattributes", ".sql",
}


def _tracked_files() -> list[str]:
    result = subprocess.run(
        ["git", "ls-files"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


@pytest.fixture(scope="module")
def tracked() -> list[str]:
    return _tracked_files()


def test_no_credential_shaped_strings_are_tracked(tracked):
    findings: list[str] = []
    for relative in tracked:
        path = REPO_ROOT / relative
        if path.suffix.lower() not in TEXT_SUFFIXES:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for name, pattern in SECRET_PATTERNS.items():
            if pattern.search(text):
                findings.append(f"{relative}: {name}")
    assert not findings, f"credential-shaped strings are tracked: {findings}"


def test_env_files_are_not_tracked(tracked):
    offenders = [
        path
        for path in tracked
        if Path(path).name == ".env"
        or (Path(path).name.startswith(".env.") and not path.endswith(".example"))
    ]
    assert not offenders, f"environment files are tracked: {offenders}"
    gitignore = (REPO_ROOT / ".gitignore").read_text(encoding="utf-8")
    assert ".env" in gitignore


def test_no_audio_or_model_binaries_are_tracked(tracked):
    offenders = [path for path in tracked if Path(path).suffix.lower() in BINARY_SUFFIXES]
    assert not offenders, f"binary media/model files are tracked: {offenders}"


def test_env_example_carries_no_values_for_secrets(tracked):
    example = REPO_ROOT / "backend" / ".env.example"
    if not example.exists():
        pytest.skip("no .env.example")
    secret_keys = (
        "STT_S3_SECRET_KEY",
        "STT_DEEPGRAM_API_KEY",
        "STT_ELEVENLABS_API_KEY",
        "STT_SARVAM_API_KEY",
        "STT_API_KEYS",
    )
    for line in example.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        if key.strip() in secret_keys:
            assert value.strip() == "", f"{key.strip()} carries a value in .env.example"


def test_the_frontend_never_receives_secrets():
    """Frontend env vars are public by construction: VITE_* only, no keys."""
    frontend = REPO_ROOT / "frontend"
    for candidate in frontend.glob(".env*"):
        for line in candidate.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                continue
            key = stripped.partition("=")[0].strip()
            assert key.startswith("VITE_"), f"{candidate.name} defines non-VITE var {key}"
            assert "KEY" not in key.upper() or "URL" in key.upper(), (
                f"{candidate.name} looks like it carries a secret: {key}"
            )

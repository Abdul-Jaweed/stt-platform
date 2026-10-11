"""Redaction for logs and span attributes.

Two hard rules from the spec: secrets never appear in logs, and neither do
audio bytes or transcript text. ``redact_text`` masks key-like tokens
inside free text; ``redact_mapping`` removes content-bearing fields
outright (masking them would still leak length and shape).
"""
from __future__ import annotations

import re
from typing import Any

MASK = "***"

# Content that must never be logged, whatever its key is called.
CONTENT_FIELDS = frozenset(
    {
        "transcript",
        "text",
        "segments",
        "words",
        "audio",
        "audio_bytes",
        "content",
        "payload",
        "file",
        "filename",
        "result",
        "body",
    }
)

# Credential-looking substrings inside otherwise loggable text.
_KEY_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"sk_[A-Za-z0-9_\-]{6,}"),
    re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._\-]{8,}"),
    # Authorization-style header values: "Token abc123def", "token xyz".
    re.compile(r"(?i)(\btoken\s+)[A-Za-z0-9._\-]{8,}"),
    re.compile(r"(?i)(token\s*[:=]\s*)\S{6,}"),
    re.compile(
        r"(?i)((?:api[-_]?(?:key|subscription[-_]?key)|secret|password)"
        r"\s*[:=]\s*)\S{4,}"
    ),
)


def redact_text(value: str) -> str:
    """Mask credential-like substrings; ordinary text is untouched."""
    redacted = value
    for pattern in _KEY_PATTERNS:
        if pattern.groups:
            redacted = pattern.sub(
                lambda match: match.group(1) + MASK
                if match.group(1) is not None
                else MASK,
                redacted,
            )
        else:
            redacted = pattern.sub(MASK, redacted)
    return redacted


def redact_mapping(mapping: dict[str, Any]) -> dict[str, Any]:
    """Drop content fields, redact credential-looking values, recurse."""
    clean: dict[str, Any] = {}
    for key, value in mapping.items():
        lowered = key.lower()
        if lowered in CONTENT_FIELDS:
            continue
        if isinstance(value, dict):
            clean[key] = redact_mapping(value)
        elif isinstance(value, str):
            clean[key] = redact_text(value)
        else:
            clean[key] = value
    return clean

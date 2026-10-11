"""Opaque object keys with tenant-scope prefixes.

Never expose physical layout to clients; keys are built here only.
"""
import re

_SAFE_SUFFIX = re.compile(r"^\.[A-Za-z0-9]{1,8}$")


def _safe_suffix(suffix: str) -> str:
    return suffix if _SAFE_SUFFIX.match(suffix or "") else ""


def original_key(scope: str, job_id: str, suffix: str) -> str:
    """Original upload: ``audio/<scope>/<job_id>/original<ext>``."""
    return f"audio/{scope}/{job_id}/original{_safe_suffix(suffix)}"


def artifact_key(scope: str, job_id: str, name: str) -> str:
    """Derived artifact: ``artifacts/<scope>/<job_id>/<name>``."""
    safe = re.sub(r"[^A-Za-z0-9._-]", "", name) or "artifact"
    return f"artifacts/{scope}/{job_id}/{safe}"

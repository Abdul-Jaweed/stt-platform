"""Opaque object keys with tenant-scope prefixes.

Never expose physical layout to clients; keys are built here only.
"""
import re

_SAFE_SUFFIX = re.compile(r"^\.[A-Za-z0-9]{1,8}$")
_ORIGINAL_KEY = re.compile(
    r"^audio/(?P<scope>[A-Za-z0-9._-]{1,64})/(?P<job>[A-Za-z0-9-]{1,64})"
    r"/original(\.[A-Za-z0-9]{1,8})?$"
)

def _safe_suffix(suffix: str) -> str:
    return suffix if _SAFE_SUFFIX.match(suffix or "") else ""


def is_original_key(scope: str, key: str) -> bool:
    """True iff ``key`` is a well-formed original-upload key for ``scope``.

    The presigned flow echoes a client-held key back; a key that does not
    belong to the caller's scope is never accepted.
    """
    match = _ORIGINAL_KEY.match(key or "")
    return bool(match) and match.group("scope") == scope


def original_key(scope: str, job_id: str, suffix: str) -> str:
    """Original upload: ``audio/<scope>/<job_id>/original<ext>``."""
    return f"audio/{scope}/{job_id}/original{_safe_suffix(suffix)}"


def artifact_key(scope: str, job_id: str, name: str) -> str:
    """Derived artifact: ``artifacts/<scope>/<job_id>/<name>``."""
    safe = re.sub(r"[^A-Za-z0-9._-]", "", name) or "artifact"
    return f"artifacts/{scope}/{job_id}/{safe}"


def evaluation_artifact_key(evaluation_id: str, name: str = "result.json") -> str:
    """Evaluation artifact: ``artifacts/evaluation/<id>/<name>``.

    Evaluations are not tenant-scoped (they are an operator-facing tool),
    so the prefix is a fixed ``evaluation`` scope and the id is opaque.
    """
    safe_id = re.sub(r"[^A-Za-z0-9._-]", "", evaluation_id) or "unknown"
    safe_name = re.sub(r"[^A-Za-z0-9._-]", "", name) or "artifact"
    return f"artifacts/evaluation/{safe_id}/{safe_name}"

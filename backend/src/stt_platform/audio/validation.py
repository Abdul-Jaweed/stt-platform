"""Upload validation: resource limits first, then real content."""
from pathlib import Path

from stt_platform.audio.inspect import AudioInfo, inspect_audio
from stt_platform.core.config import Settings
from stt_platform.domain.errors import AudioTooLong, UploadTooLarge


def validate_upload(
    path: Path, settings: Settings, *, max_duration_s: float | None = None
) -> AudioInfo:
    """Validate an uploaded file against limits and return its metadata.

    The size limit is enforced before any probe runs; content is then
    inspected with ffprobe (extensions are never trusted). ``max_duration_s``
    overrides the default synchronous duration limit (used by the async
    worker path, which is allowed to process much longer audio).
    """
    path = Path(path)
    size_bytes = path.stat().st_size if path.is_file() else 0
    if size_bytes > settings.max_upload_bytes:
        raise UploadTooLarge(
            f"file is {size_bytes} bytes; limit is {settings.max_upload_bytes}"
        )

    info = inspect_audio(path)

    if max_duration_s is None:
        if info.duration_s > settings.max_sync_duration_s:
            raise AudioTooLong(
                f"audio is {info.duration_s:.1f}s; the Phase 1 synchronous "
                f"limit is {settings.max_sync_duration_s:.0f}s"
            )
    elif info.duration_s > max_duration_s:
        raise AudioTooLong(
            f"audio is {info.duration_s:.1f}s; the limit is {max_duration_s:.0f}s"
        )
    return info

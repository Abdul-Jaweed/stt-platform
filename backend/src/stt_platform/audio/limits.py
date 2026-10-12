"""Provider-declared limits, checked before a vendor call.

Every populated limit traces to the vendor's own documentation (the source is
recorded in ``docs/providers.md``); ``None`` means the vendor documents no
limit, and this module refuses nothing on the platform's behalf — an invented
limit would reject audio the provider would happily transcribe.

The platform's own caps (``max_upload_bytes`` on arrival, the sync/async
duration split) are separate and always apply. These are the *provider's*.
"""
from stt_platform.audio.inspect import AudioInfo
from stt_platform.domain.errors import AudioTooLong, UploadTooLarge
from stt_platform.schemas.providers import ProviderCapabilities


def check_provider_limits(
    info: AudioInfo, capabilities: ProviderCapabilities, *, provider_id: str
) -> None:
    """Refuse audio the provider itself would refuse, quoting its own numbers.

    Raises :class:`AudioTooLong` / :class:`UploadTooLarge` naming the provider
    and the limit, so the failure is actionable ("sarvam accepts at most
    7200s") instead of a vendor-side 400 discovered after the upload.
    """
    cap_s = capabilities.max_duration_s
    if cap_s is not None and info.duration_s > cap_s:
        raise AudioTooLong(
            f"provider '{provider_id}' accepts at most {cap_s:.0f}s of audio; "
            f"this file is {info.duration_s:.1f}s"
        )
    cap_bytes = capabilities.max_file_bytes
    if cap_bytes is not None and info.size_bytes > cap_bytes:
        raise UploadTooLarge(
            f"provider '{provider_id}' accepts files up to {cap_bytes} bytes; "
            f"this file is {info.size_bytes} bytes"
        )

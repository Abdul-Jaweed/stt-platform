"""Provider discovery endpoint."""
from fastapi import APIRouter, Depends, Request

from stt_platform.api.security import get_scope

router = APIRouter(
    prefix="/providers", tags=["providers"], dependencies=[Depends(get_scope)]
)


@router.get("")
def list_providers(request: Request) -> dict:
    providers = request.app.state.registry.all()
    settings = request.app.state.settings
    return {
        "providers": [p.model_dump(mode="json") for p in providers],
        # The platform's own caps, so a client can tell "this deployment"
        # from "this provider" when it explains a refusal (``docs/api.md``).
        "limits": {
            "max_upload_bytes": settings.max_upload_bytes,
            "max_sync_duration_s": settings.max_sync_duration_s,
            "max_async_duration_s": settings.max_async_duration_s,
            "max_live_preview_duration_s": settings.max_live_preview_duration_s,
        },
    }

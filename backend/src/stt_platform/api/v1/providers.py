"""Provider discovery endpoint."""
from fastapi import APIRouter, Depends, Request

from stt_platform.api.security import get_scope

router = APIRouter(
    prefix="/providers", tags=["providers"], dependencies=[Depends(get_scope)]
)


@router.get("")
def list_providers(request: Request) -> dict:
    providers = request.app.state.registry.all()
    return {"providers": [p.model_dump(mode="json") for p in providers]}

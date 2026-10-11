"""Provider discovery endpoint."""
from fastapi import APIRouter, Request

router = APIRouter(prefix="/providers", tags=["providers"])


@router.get("")
def list_providers(request: Request) -> dict:
    providers = request.app.state.registry.all()
    return {"providers": [p.model_dump(mode="json") for p in providers]}

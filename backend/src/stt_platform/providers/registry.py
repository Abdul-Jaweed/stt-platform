"""Provider registry: explicit selection by id (routing comes later)."""
from stt_platform.domain.errors import ProviderNotFound
from stt_platform.providers.base import ProviderProtocol
from stt_platform.schemas.providers import ProviderInfo


class ProviderRegistry:
    def __init__(self) -> None:
        self._providers: dict[str, ProviderProtocol] = {}
        self._default_id: str | None = None

    def register(self, provider: ProviderProtocol, *, default: bool = False) -> None:
        if provider.id in self._providers:
            raise ValueError(f"provider already registered: {provider.id!r}")
        self._providers[provider.id] = provider
        if default or self._default_id is None:
            self._default_id = provider.id

    def get(self, provider_id: str | None = None) -> ProviderProtocol:
        pid = provider_id or self._default_id
        if pid is None or pid not in self._providers:
            raise ProviderNotFound(pid or "<none>")
        return self._providers[pid]

    @property
    def default_id(self) -> str | None:
        return self._default_id

    def all(self) -> list[ProviderInfo]:
        return [
            ProviderInfo(
                id=p.id,
                name=p.name,
                is_local=p.is_local,
                capabilities=p.capabilities(),
                health=p.health(),
            )
            for p in self._providers.values()
        ]

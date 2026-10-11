import pytest

from stt_platform.domain.errors import ProviderNotFound
from stt_platform.providers.registry import ProviderRegistry
from tests.fakes import FakeProvider


class OtherFake(FakeProvider):
    id = "other"
    name = "Other Provider"


def test_register_and_get():
    reg = ProviderRegistry()
    fp = FakeProvider()
    reg.register(fp)
    assert reg.get("fake") is fp
    assert reg.get() is fp  # default
    assert reg.default_id == "fake"


def test_unknown_provider_raises():
    reg = ProviderRegistry()
    reg.register(FakeProvider())
    with pytest.raises(ProviderNotFound):
        reg.get("nope")


def test_empty_registry_default_raises():
    reg = ProviderRegistry()
    with pytest.raises(ProviderNotFound):
        reg.get()


def test_duplicate_id_rejected():
    reg = ProviderRegistry()
    reg.register(FakeProvider())
    with pytest.raises(ValueError):
        reg.register(FakeProvider())


def test_all_returns_provider_info():
    reg = ProviderRegistry()
    reg.register(FakeProvider())
    reg.register(OtherFake(), default=True)
    infos = reg.all()
    assert {i.id for i in infos} == {"fake", "other"}
    assert reg.default_id == "other"
    assert all(i.capabilities.segment_timestamps for i in infos)
    assert all(i.health.status == "ok" for i in infos)

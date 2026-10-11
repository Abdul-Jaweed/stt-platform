"""Registry wiring: which providers exist depends on configured keys."""
from stt_platform.providers.defaults import build_default_registry
from stt_platform.providers.registry import ProviderRegistry
from tests.integration.harness import make_settings


def _settings(tmp_path, **overrides):
    base = make_settings(tmp_path).model_copy(
        update={
            "deepgram_api_key": None,
            "elevenlabs_api_key": None,
            "sarvam_api_key": None,
        }
    )
    return base.model_copy(update=overrides) if overrides else base


def test_registry_without_keys_registers_only_local_provider(tmp_path):
    registry = build_default_registry(_settings(tmp_path))

    ids = {info.id for info in registry.all()}
    assert ids == {"faster-whisper"}
    assert registry.default_id == "faster-whisper"


def test_build_default_registry_returns_provider_registry(tmp_path):
    assert isinstance(build_default_registry(_settings(tmp_path)), ProviderRegistry)


def test_registry_registers_deepgram_when_key_set(tmp_path):
    registry = build_default_registry(
        _settings(tmp_path, deepgram_api_key="dg-test-key")
    )

    ids = {info.id for info in registry.all()}
    assert ids == {"faster-whisper", "deepgram"}
    assert registry.default_id == "faster-whisper"
    deepgram = registry.get("deepgram")
    assert deepgram.is_local is False


def test_registry_registers_elevenlabs_when_key_set(tmp_path):
    registry = build_default_registry(
        _settings(tmp_path, elevenlabs_api_key="el-test-key")
    )

    ids = {info.id for info in registry.all()}
    assert ids == {"faster-whisper", "elevenlabs"}


def test_registry_registers_sarvam_when_key_set(tmp_path):
    registry = build_default_registry(_settings(tmp_path, sarvam_api_key="srv-key"))

    ids = {info.id for info in registry.all()}
    assert ids == {"faster-whisper", "sarvam"}
    assert registry.get("sarvam").capabilities().async_processing is True


def test_registry_registers_all_when_all_keys_set(tmp_path):
    registry = build_default_registry(
        _settings(
            tmp_path,
            deepgram_api_key="dg",
            elevenlabs_api_key="el",
            sarvam_api_key="srv",
        )
    )

    ids = {info.id for info in registry.all()}
    assert ids == {"faster-whisper", "deepgram", "elevenlabs", "sarvam"}
    assert registry.default_id == "faster-whisper"

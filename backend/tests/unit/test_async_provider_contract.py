"""The async provider contract: protocol shape + service routing rules."""
import pytest

from stt_platform.providers.base import (
    AsyncProviderProtocol,
    RemoteJobRef,
    RemoteJobState,
)
from tests.fakes import FakeAsyncProvider, FakeProvider


def test_fake_async_provider_satisfies_the_protocol():
    provider = FakeAsyncProvider()

    assert isinstance(provider, AsyncProviderProtocol)

    submitted = provider.submit(request=None)  # type: ignore[arg-type]
    assert isinstance(submitted, RemoteJobRef)
    assert submitted.provider_job_id == "remote-1"

    state = provider.poll(submitted)
    assert isinstance(state, RemoteJobState)
    assert state.state == "pending"

    assert provider.cancel(submitted) is True
    assert provider.submits == 1
    assert provider.polls == 1
    assert provider.cancels == 1


def test_sync_provider_is_not_async_capable():
    provider = FakeProvider()

    assert not isinstance(provider, AsyncProviderProtocol)


def test_async_provider_advertises_async_processing():
    provider = FakeAsyncProvider()

    capabilities = provider.capabilities()

    assert capabilities.async_processing is True
    assert capabilities.capability_sources["async_processing"] == "documented"


def test_cancel_unsupported_is_reported_honestly():
    provider = FakeAsyncProvider(cancel_supported=False)
    ref = provider.submit(request=None)  # type: ignore[arg-type]

    assert provider.cancel(ref) is False


def test_poll_script_consumes_then_repeats_last_entry():
    provider = FakeAsyncProvider(poll_script=["pending", "completed"])
    ref = provider.submit(request=None)  # type: ignore[arg-type]

    states = [provider.poll(ref).state for _ in range(4)]

    assert states == ["pending", "completed", "completed", "completed"]


def test_submit_failures_carry_retryability():
    transient = FakeAsyncProvider(fail_submit="transient")
    with pytest.raises(Exception) as err:
        transient.submit(request=None)  # type: ignore[arg-type]
    assert getattr(err.value, "retryable", None) is True

    permanent = FakeAsyncProvider(fail_submit="permanent")
    with pytest.raises(Exception) as err2:
        permanent.submit(request=None)  # type: ignore[arg-type]
    assert getattr(err2.value, "retryable", None) is False

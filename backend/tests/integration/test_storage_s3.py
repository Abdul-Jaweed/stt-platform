"""S3-compatible object storage client against an ephemeral RustFS."""
import httpx
import pytest

from stt_platform.storage.keys import artifact_key, original_key
from stt_platform.storage.s3 import S3ObjectStore
from tests.rustfs_container import ACCESS_KEY, SECRET_KEY, RustFSContainer


@pytest.fixture(scope="module")
def rustfs():
    with RustFSContainer() as rc:
        yield rc


@pytest.fixture()
def store(rustfs):
    s = S3ObjectStore(
        endpoint_url=rustfs.s3_endpoint(),
        access_key=ACCESS_KEY,
        secret_key=SECRET_KEY,
        region="us-east-1",
        bucket_audio="stt-audio",
        bucket_artifacts="stt-artifacts",
    )
    s.ensure_buckets()
    return s


def test_put_get_delete_roundtrip(store, tmp_path):
    f = tmp_path / "a.bin"
    f.write_bytes(b"hello s3")
    key = original_key("local", "job-1", ".wav")
    store.put_file("stt-audio", key, f)
    assert store.exists("stt-audio", key)
    assert store.get_bytes("stt-audio", key) == b"hello s3"
    store.delete("stt-audio", key)
    assert not store.exists("stt-audio", key)


def test_presigned_put_is_usable_with_plain_http(store):
    key = artifact_key("local", "job-2", "note.txt")
    url = store.presign_put("stt-artifacts", key, expires_s=300)
    r = httpx.put(url, content=b"via presign")
    assert r.status_code == 200
    assert store.get_bytes("stt-artifacts", key) == b"via presign"


def test_ensure_buckets_is_idempotent(store):
    store.ensure_buckets()  # second call must not raise
    assert store.exists("stt-audio", "__no_such_key__") is False


def test_keys_are_opaque_and_scoped():
    assert original_key("local", "j", ".wav") == "audio/local/j/original.wav"
    assert original_key("local", "j", "../evil") == "audio/local/j/original"
    assert (
        artifact_key("local", "j", "transcript.json")
        == "artifacts/local/j/transcript.json"
    )
    assert artifact_key("local", "j", "we ird/na me!") == (
        "artifacts/local/j/weirdname"
    )

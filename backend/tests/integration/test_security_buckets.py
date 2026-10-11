"""Security hardening: the storage buckets are private, and stay private.

The spec's red lines include "buckets are not public" and "audio is never
exposed". This asserts the real RustFS deployment, not a mock: an anonymous
HTTP read must be refused, the bucket ACL must carry no public grant, and no
bucket policy may name a wildcard principal.
"""
from __future__ import annotations

import httpx
import pytest

from tests.integration.harness import build_store
from tests.rustfs_container import RustFSContainer


@pytest.fixture(scope="module")
def rustfs():
    with RustFSContainer() as container:
        yield container


@pytest.fixture(scope="module")
def store(rustfs):
    return build_store(rustfs)


@pytest.fixture(scope="module")
def seeded(store):
    """One object in each bucket, so "not public" is a real read attempt."""
    store.put_bytes(store.bucket_audio, "local/security/probe.wav", b"RIFF....")
    store.put_bytes(store.bucket_artifacts, "local/security/probe.json", b"{}")
    return store


def _anonymous_get(url: str) -> int:
    response = httpx.get(url, timeout=10.0)
    return response.status_code


def test_anonymous_reads_are_refused(seeded, rustfs):
    for bucket, key in (
        (seeded.bucket_audio, "local/security/probe.wav"),
        (seeded.bucket_artifacts, "local/security/probe.json"),
    ):
        url = f"{rustfs.s3_endpoint()}/{bucket}/{key}"
        assert _anonymous_get(url) in (403, 404), (
            f"anonymous GET of {bucket}/{key} was not refused"
        )


def test_bucket_acl_has_no_public_grant(seeded):
    for bucket in (seeded.bucket_audio, seeded.bucket_artifacts):
        acl = seeded._client.get_bucket_acl(Bucket=bucket)
        for grant in acl.get("Grants", []):
            grantee = grant.get("Grantee", {})
            uri = grantee.get("URI", "")
            assert "AllUsers" not in uri, f"{bucket} grants public read"
            assert "AuthenticatedUsers" not in uri, f"{bucket} grants authenticated read"


def test_no_bucket_policy_names_a_wildcard_principal(seeded):
    import json

    for bucket in (seeded.bucket_audio, seeded.bucket_artifacts):
        try:
            policy = seeded._client.get_bucket_policy(Bucket=bucket)
        except Exception:
            continue  # no policy at all is the safe state
        document = json.loads(policy["Policy"])
        for statement in document.get("Statement", []):
            principal = statement.get("Principal")
            assert principal != "*", f"{bucket} policy allows a wildcard principal"
            assert not (
                isinstance(principal, dict) and "*" in principal.get("AWS", [])
            ), f"{bucket} policy allows a wildcard principal"

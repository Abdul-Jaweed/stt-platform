"""S3-compatible object storage client (boto3; RustFS in dev, any S3 API).

Buckets are created on demand; keys are opaque (see ``keys``); nothing
in this module ever logs object contents.
"""
from __future__ import annotations

from pathlib import Path

import boto3
from botocore.client import Config as BotoConfig
from botocore.exceptions import ClientError

from stt_platform.core.config import Settings


class S3ObjectStore:
    def __init__(
        self,
        *,
        endpoint_url: str,
        access_key: str,
        secret_key: str,
        region: str,
        bucket_audio: str,
        bucket_artifacts: str,
        use_tls: bool = False,
    ) -> None:
        self.bucket_audio = bucket_audio
        self.bucket_artifacts = bucket_artifacts
        self._client = boto3.client(
            "s3",
            endpoint_url=endpoint_url,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            region_name=region,
            verify=use_tls,
            config=BotoConfig(
                # Pin SigV4: the default (SigV2) presigned URLs sign the
                # request's Content-Type into the signature, so any client
                # sending e.g. ``Content-Type: audio/wav`` gets 403
                # SignatureDoesNotMatch. SigV4 presigns are content-type
                # agnostic (only the host is signed).
                signature_version="s3v4",
                s3={"addressing_style": "path"},
                retries={"max_attempts": 3, "mode": "standard"},
                connect_timeout=5,
                read_timeout=60,
            ),
        )

    @classmethod
    def from_settings(cls, settings: Settings) -> "S3ObjectStore":
        return cls(
            endpoint_url=settings.s3_endpoint,
            access_key=settings.s3_access_key,
            secret_key=settings.s3_secret_key,
            region=settings.s3_region,
            bucket_audio=settings.s3_bucket_audio,
            bucket_artifacts=settings.s3_bucket_artifacts,
        )

    # ------------------------------------------------------------------
    # buckets
    # ------------------------------------------------------------------
    def ensure_buckets(self) -> None:
        for bucket in (self.bucket_audio, self.bucket_artifacts):
            try:
                self._client.head_bucket(Bucket=bucket)
            except ClientError:
                self._client.create_bucket(Bucket=bucket)

    # ------------------------------------------------------------------
    # objects
    # ------------------------------------------------------------------
    def put_file(self, bucket: str, key: str, path: Path) -> None:
        self._client.upload_file(str(path), bucket, key)

    def put_bytes(
        self,
        bucket: str,
        key: str,
        data: bytes,
        content_type: str | None = None,
    ) -> None:
        self._client.put_object(
            Bucket=bucket,
            Key=key,
            Body=data,
            ContentType=content_type or "application/octet-stream",
        )

    def get_bytes(self, bucket: str, key: str) -> bytes:
        return self._client.get_object(Bucket=bucket, Key=key)["Body"].read()

    def exists(self, bucket: str, key: str) -> bool:
        try:
            self._client.head_object(Bucket=bucket, Key=key)
        except ClientError:
            return False
        return True

    def delete(self, bucket: str, key: str) -> None:
        self._client.delete_object(Bucket=bucket, Key=key)

    def presign_put(self, bucket: str, key: str, expires_s: int = 900) -> str:
        return self._client.generate_presigned_url(
            "put_object",
            Params={"Bucket": bucket, "Key": key},
            ExpiresIn=expires_s,
        )

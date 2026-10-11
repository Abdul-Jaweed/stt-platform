"""RustFS (S3-compatible) as a testcontainers-managed container.

Reuses the exact engine and image the dev compose stack runs; MinIO's
community images were archived upstream, so integration tests target
RustFS directly.
"""
import time

import httpx
from testcontainers.core.container import DockerContainer

IMAGE = "rustfs/rustfs:1.0.1"
ACCESS_KEY = "stt"
SECRET_KEY = "stt123"


class RustFSContainer(DockerContainer):
    def __init__(self, image: str = IMAGE) -> None:
        super().__init__(image)
        self.with_env("RUSTFS_VOLUMES", "/data")
        self.with_env("RUSTFS_ADDRESS", "0.0.0.0:9000")
        self.with_env("RUSTFS_ACCESS_KEY", ACCESS_KEY)
        self.with_env("RUSTFS_SECRET_KEY", SECRET_KEY)
        self.with_exposed_ports(9000)

    def s3_endpoint(self) -> str:
        return (
            f"http://{self.get_container_host_ip()}:"
            f"{self.get_exposed_port(9000)}"
        )

    def start(self) -> "RustFSContainer":
        super().start()
        deadline = time.monotonic() + 40
        while time.monotonic() < deadline:
            try:
                r = httpx.get(f"{self.s3_endpoint()}/health", timeout=2)
                if r.status_code == 200:
                    return self
            except Exception:
                pass
            time.sleep(0.5)
        raise RuntimeError("RustFS did not become healthy within 40s")

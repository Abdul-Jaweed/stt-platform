"""Phase 4 end-to-end: a managed asynchronous provider through the real stack.

An in-process **fake Sarvam server** emulates the documented batch flow
(initiate -> upload-files -> presigned PUT -> start -> status ->
download-files -> presigned GET). The API (uvicorn :8210) and the Temporal
worker are started as child processes with ``STT_SARVAM_API_KEY`` and
``STT_SARVAM_BASE_URL`` pointing at the fake, then the script:

1. checks ``/v1/providers`` lists ``sarvam`` only because the key is set,
2. creates a transcription of ``audio/sample.wav`` requesting ``sarvam``
   (an async-capable provider must be routed to the 202/workflow path),
3. polls to a terminal status and fetches the canonical result,
4. verifies the ``provider_attempts`` row (exactly one, COMPLETED),
5. writes ``results/phase4_e2e/result.json``.

No paid API calls: the whole Sarvam surface is local.

Run (foreground, from the repo root):

    cd backend && uv run python ../scripts/e2e_phase4.py

Requires: compose stack up (postgres/rustfs/temporal), ffmpeg on PATH.
"""
from __future__ import annotations

import json
import os
import subprocess
import tempfile
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
SAMPLE = ROOT / "audio" / "sample.wav"
OUT_DIR = ROOT / "results" / "phase4_e2e"
API = "http://127.0.0.1:8210"
FAKE = "http://127.0.0.1:8765"
FAKE_PORT = 8765


def probe_duration_bytes(data: bytes) -> float:
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as handle:
        handle.write(data)
        path = Path(handle.name)
    try:
        out = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(path),
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        return float(out.stdout.strip())
    finally:
        path.unlink(missing_ok=True)


class FakeSarvam:
    """In-process stand-in for the documented Sarvam batch STT surface."""

    def __init__(self) -> None:
        self.audio: dict[str, bytes] = {}
        self.duration_s = 0.0
        self.status_calls = 0
        self.requests: list[str] = []

    # ------------------------------------------------------------------
    def result_payload(self) -> dict:
        duration = max(0.5, self.duration_s)
        mid = round(duration * 0.5, 3)
        end = round(max(mid + 0.01, duration - 0.05), 3)
        first = "Hello, I'm Abdul Javid, AI Engineer at Home and Managed."
        second = "This is a fake Sarvam e2e transcription."
        return {
            "request_id": "e2e-fake-1",
            "transcript": f"{first} {second}",
            "language_code": "en-IN",
            "timestamps": {
                "chunks": [first, second],
                "start_time_seconds": [0.0, mid],
                "end_time_seconds": [mid, end],
            },
            "diarized_transcript": {
                "entries": [
                    {
                        "transcript": first,
                        "start_time_seconds": 0.0,
                        "end_time_seconds": mid,
                        "speaker_id": "0",
                    },
                    {
                        "transcript": second,
                        "start_time_seconds": mid,
                        "end_time_seconds": end,
                        "speaker_id": "1",
                    },
                ]
            },
        }

    # ------------------------------------------------------------------
    def handler(self):
        outer = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, format, *args):  # noqa: A002 - http.server API
                pass

            def _send(self, code: int, payload: dict) -> None:
                body = json.dumps(payload).encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _body(self) -> bytes:
                length = int(self.headers.get("Content-Length") or 0)
                return self.rfile.read(length) if length else b""

            def do_PUT(self):  # noqa: N802 - http.server API
                name = self.path.rsplit("/", 1)[-1]
                data = self._body()
                outer.audio[name] = data
                outer.requests.append(f"PUT {self.path} ({len(data)} bytes)")
                outer.duration_s = probe_duration_bytes(data)
                self._send(201, {})

            def do_POST(self):  # noqa: N802 - http.server API
                path = self.path.split("?")[0]
                outer.requests.append(f"POST {path}")
                body = self._body()
                if path == "/speech-to-text/job/v1":
                    self._send(
                        202,
                        {
                            "job_id": "job-e2e-1",
                            "job_state": "Accepted",
                            "storage_container_type": "Azure_V1",
                            "job_parameters": {},
                        },
                    )
                elif path == "/speech-to-text/job/v1/upload-files":
                    names = (json.loads(body or b"{}")).get("files") or ["audio.wav"]
                    self._send(
                        200,
                        {
                            "job_id": "job-e2e-1",
                            "job_state": "Accepted",
                            "upload_urls": {
                                name: {"file_url": f"{FAKE}/storage/{name}"}
                                for name in names
                            },
                        },
                    )
                elif path.endswith("/start"):
                    self._send(
                        200, {"job_id": "job-e2e-1", "job_state": "Running"}
                    )
                elif path == "/speech-to-text/job/v1/download-files":
                    self._send(
                        200,
                        {
                            "job_id": "job-e2e-1",
                            "job_state": "Completed",
                            "download_urls": {
                                "0.json": {"file_url": f"{FAKE}/download/0.json"}
                            },
                        },
                    )
                else:
                    self._send(
                        404, {"error": {"message": "unknown", "code": "not_found_error"}}
                    )

            def do_GET(self):  # noqa: N802 - http.server API
                path = self.path.split("?")[0]
                outer.requests.append(f"GET {path}")
                if path.endswith("/status"):
                    outer.status_calls += 1
                    state = "Pending" if outer.status_calls <= 1 else "Completed"
                    payload: dict = {
                        "job_id": "job-e2e-1",
                        "job_state": state,
                        "storage_container_type": "Azure_V1",
                    }
                    if state == "Completed":
                        payload["job_details"] = [
                            {
                                "file_name": "sample.wav",
                                "status": "success",
                                "outputs": [{"file_name": "0.json"}],
                            }
                        ]
                    self._send(200, payload)
                elif path == "/download/0.json":
                    self._send(200, outer.result_payload())
                else:
                    self._send(
                        404, {"error": {"message": "unknown", "code": "not_found_error"}}
                    )

        return Handler


def kill_tree(proc) -> None:
    if proc is None:
        return
    subprocess.run(
        ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
        capture_output=True,
        text=True,
    )


def wait_http(url: str, timeout_s: float) -> None:
    deadline = time.time() + timeout_s
    last: Exception | None = None
    while time.time() < deadline:
        try:
            response = httpx.get(url, timeout=5.0)
            if response.status_code < 500:
                return
        except Exception as exc:  # noqa: BLE001 - retried until the deadline
            last = exc
        time.sleep(1.0)
    raise RuntimeError(f"{url} not ready after {timeout_s}s ({last!r})")


def wait_log_contains(path: Path, needle: str, timeout_s: float) -> bool:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if path.exists() and needle in path.read_text(errors="ignore"):
            return True
        time.sleep(0.5)
    return False


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="stt-e2e4-"))
    results: dict = {
        "phase": 4,
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "api_base": API,
        "fake_sarvam": FAKE,
        "sample": str(SAMPLE),
        "checks": {},
    }
    checks = results["checks"]

    fake = FakeSarvam()
    server = ThreadingHTTPServer(("127.0.0.1", FAKE_PORT), fake.handler())
    threading.Thread(target=server.serve_forever, daemon=True).start()

    api_proc = worker_proc = None
    env = {
        **os.environ,
        "STT_SARVAM_API_KEY": "e2e-test-key",
        "STT_SARVAM_BASE_URL": FAKE,
    }
    try:
        print("[e2e4] applying migrations (alembic upgrade head) ...")
        subprocess.run(
            ["uv", "run", "alembic", "upgrade", "head"],
            cwd=BACKEND,
            check=True,
            capture_output=True,
            text=True,
        )

        api_log = open(tmp / "api.log", "w")  # noqa: SIM115
        print(f"[e2e4] starting API on {API} ...")
        api_proc = subprocess.Popen(
            [
                "uv",
                "run",
                "python",
                "-m",
                "uvicorn",
                "stt_platform.main:app",
                "--port",
                "8210",
                "--log-level",
                "warning",
            ],
            cwd=BACKEND,
            stdout=api_log,
            stderr=subprocess.STDOUT,
            env=env,
        )
        wait_http(f"{API}/v1/health/live", 90)
        print("[e2e4] API ready")

        worker_log_path = tmp / "worker.log"
        worker_log = open(worker_log_path, "w")  # noqa: SIM115
        print("[e2e4] starting Temporal worker ...")
        worker_proc = subprocess.Popen(
            [
                "uv",
                "run",
                "python",
                "-m",
                "stt_platform.workers.transcription_worker",
            ],
            cwd=BACKEND,
            stdout=worker_log,
            stderr=subprocess.STDOUT,
            env=env,
        )
        ready = wait_log_contains(
            worker_log_path, "transcription worker up", 90
        )
        checks["worker_ready"] = ready
        if not ready:
            results["worker_log_tail"] = worker_log_path.read_text(
                errors="ignore"
            )[-2000:]
            raise RuntimeError("worker did not report readiness")

        client = httpx.Client(base_url=API, timeout=120.0)

        providers = [
            p["id"] for p in client.get("/v1/providers").json()["providers"]
        ]
        results["providers"] = providers
        checks["sarvam_registered"] = "sarvam" in providers

        print("[e2e4] creating a sarvam transcription (expect 202) ...")
        with SAMPLE.open("rb") as handle:
            response = client.post(
                "/v1/transcriptions",
                files={"file": (SAMPLE.name, handle, "audio/wav")},
                data={"provider": "sarvam"},
                headers={"Idempotency-Key": str(uuid.uuid4())},
            )
        checks["create_status_202"] = response.status_code == 202
        job = response.json()
        job_id = job["id"]
        results["job"] = {
            "id": job_id,
            "create_http_status": response.status_code,
            "initial_status": job["status"],
            "requested_provider": job["requested_provider"],
        }

        deadline = time.time() + 240.0
        status = job["status"]
        while status not in ("SUCCEEDED", "FAILED", "CANCELLED"):
            if time.time() > deadline:
                break
            time.sleep(1.0)
            status = client.get(f"/v1/transcriptions/{job_id}").json()["status"]
        results["job"]["final_status"] = status
        checks["succeeded"] = status == "SUCCEEDED"

        if status == "SUCCEEDED":
            canonical = client.get(
                f"/v1/transcriptions/{job_id}/result"
            ).json()
            checks["provider_is_sarvam"] = canonical["provider"] == "sarvam"
            checks["model_is_saaras"] = canonical["model"] == "saaras:v4"
            checks["granularity_segment"] = (
                canonical["timestamp_granularity"] == "segment"
            )
            checks["has_segments"] = len(canonical["segments"]) >= 1
            checks["speakers"] = [
                segment.get("speaker") for segment in canonical["segments"]
            ]
            results["transcript"] = {
                "text": canonical["text"],
                "language": canonical["language"],
                "segments": len(canonical["segments"]),
                "audio_duration_s": canonical["audio_duration_s"],
                "warnings": canonical.get("warnings", []),
            }

        # provider_attempts: exactly one row, COMPLETED, real provider job id.
        import psycopg

        from stt_platform.core.config import get_settings

        dsn = get_settings().database_url.replace(
            "postgresql+psycopg://", "postgresql://"
        )
        with psycopg.connect(dsn) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT attempt, state, provider_job_id FROM "
                    "provider_attempts WHERE job_id = %s ORDER BY attempt",
                    (job_id,),
                )
                rows = cur.fetchall()
        results["provider_attempts"] = [
            {"attempt": r[0], "state": r[1], "provider_job_id": r[2]}
            for r in rows
        ]
        checks["attempts_rows"] = len(rows)
        checks["attempts_completed"] = (
            len(rows) == 1 and rows[0][1] == "COMPLETED"
        )
        checks["attempts_provider_job_id"] = bool(rows) and rows[0][2] == "job-e2e-1"

        results["fake_requests"] = fake.requests
        checks["fake_saw_full_flow"] = all(
            any(entry.startswith(prefix) for entry in fake.requests)
            for prefix in (
                "POST /speech-to-text/job/v1",
                "POST /speech-to-text/job/v1/upload-files",
                "PUT /storage/",
                "POST /speech-to-text/job/v1/job-e2e-1/start",
                "GET /speech-to-text/job/v1/job-e2e-1/status",
                "POST /speech-to-text/job/v1/download-files",
                "GET /download/0.json",
            )
        )

        results["passed"] = all(
            value
            for key, value in checks.items()
            if key != "speakers" and isinstance(value, bool)
        )
    finally:
        kill_tree(worker_proc)
        kill_tree(api_proc)
        server.shutdown()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "result.json").write_text(
        json.dumps(results, indent=2), encoding="utf-8"
    )
    print(json.dumps(results, indent=2))
    return 0 if results.get("passed") else 1


if __name__ == "__main__":
    raise SystemExit(main())

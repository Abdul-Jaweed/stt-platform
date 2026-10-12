#!/usr/bin/env python
"""Phase 9 / S2 evidence: tracing is on by default and never breaks a job.

Runs against the live stack (console on :8080). Three movements:

1. A real upload with the shipped default (`otlp` aimed at an endpoint
   nothing is listening on) must SUCCEED -- tracing may not become a
   failure mode.
2. A second upload, then an asynchronous one (a file past the sync cap),
   while the API and worker export spans to the console.
3. Everything collected lands in ``results/observability/``: the raw
   container logs plus a summary extracted from them.

Usage:  cd backend && uv run python ../scripts/observability_evidence.py
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
OUT = REPO_ROOT / "results" / "observability"
CONSOLE = "http://127.0.0.1:8080"
API = "http://127.0.0.1:8200"


def sh(*args: str, check: bool = True) -> str:
    proc = subprocess.run(args, capture_output=True, text=True, check=False)
    if check and proc.returncode != 0:
        raise SystemExit(f"{' '.join(args)} failed: {proc.stderr.strip()[:400]}")
    return proc.stdout


def env_value(name: str) -> str:
    for line in (REPO_ROOT / ".env").read_text(encoding="utf-8").splitlines():
        if line.startswith(f"{name}="):
            return line.split("=", 1)[1].strip()
    return ""


def set_env(name: str, value: str) -> None:
    path = REPO_ROOT / ".env"
    lines = path.read_text(encoding="utf-8").splitlines()
    out = []
    found = False
    for line in lines:
        if line.startswith(f"{name}="):
            out.append(f"{name}={value}")
            found = True
        else:
            out.append(line)
    if not found:
        out.append(f"{name}={value}")
    path.write_text("\n".join(out) + "\n", encoding="utf-8")


def restart_api_worker() -> None:
    sh("docker", "compose", "up", "-d", "--force-recreate", "api", "worker")
    deadline = time.time() + 240
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(
                f"{API}/v1/health/ready", timeout=3
            ) as response:
                if response.status == 200:
                    return
        except Exception:
            time.sleep(3)
    raise SystemExit("the API never became ready")


def upload(path: Path, timeout: int = 600) -> dict:
    boundary = "----s2evidence"
    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{path.name}"\r\n'
        f"Content-Type: audio/wav\r\n\r\n"
    ).encode() + path.read_bytes() + f"\r\n--{boundary}--\r\n".encode()
    request = urllib.request.Request(
        f"{CONSOLE}/v1/transcriptions",
        data=body,
        method="POST",
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read())


def poll(job_id: str, timeout: int = 600) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        request = urllib.request.Request(f"{CONSOLE}/v1/transcriptions/{job_id}")
        with urllib.request.urlopen(request, timeout=10) as response:
            record = json.loads(response.read())
        if record["status"] in {"SUCCEEDED", "FAILED", "CANCELLED"}:
            return record
        time.sleep(3)
    raise SystemExit(f"job {job_id} never reached a terminal state")


def make_long_wav(dest: Path, seconds: float) -> None:
    if dest.is_file():
        return
    sh(
        "ffmpeg", "-y", "-v", "error", "-f", "lavfi",
        "-i", f"sine=frequency=440:duration={int(seconds)}",
        "-ar", "16000", "-ac", "1", str(dest),
    )


def container_log(name: str) -> str:
    """Everything a container has said, stdout and stderr interleaved."""
    proc = subprocess.run(
        ["docker", "logs", name],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        check=False,
    )
    return proc.stdout


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    checks: list[tuple[str, bool, str]] = []

    sample = REPO_ROOT / "audio" / "sample.wav"
    long_wav = OUT / "long_240s.wav"
    make_long_wav(long_wav, 240)

    # ---- movement 1: default otlp endpoint, nothing listening --------------
    set_env("STT_OTEL_EXPORTER", "otlp")
    restart_api_worker()
    dead_ok = env_value("STT_OTEL_ENDPOINT") or "http://127.0.0.1:4318 (compose default)"
    first = upload(sample)
    record = poll(first["id"])
    checks.append((
        "a real upload succeeds with tracing on and a dead OTLP endpoint",
        record["status"] == "SUCCEEDED",
        f"job {record['id']} -> {record['status']} (endpoint {dead_ok})",
    ))

    # ---- movement 2: console exporter, real spans --------------------------
    set_env("STT_OTEL_EXPORTER", "console")
    restart_api_worker()
    api_before = len(container_log("stt-api"))
    worker_before = len(container_log("stt-worker"))

    sync_job = poll(upload(sample)["id"])
    async_first = upload(long_wav)
    async_job = poll(async_first["id"])
    time.sleep(8)  # let the batch processors flush

    api_log = container_log("stt-api")[api_before:]
    worker_log = container_log("stt-worker")[worker_before:]
    (OUT / "api-spans.log").write_text(api_log, encoding="utf-8")
    (OUT / "worker-spans.log").write_text(worker_log, encoding="utf-8")

    def span_names(text: str) -> set[str]:
        return set(re.findall(r'"name":\s*"([^"]+)"', text))

    api_spans = span_names(api_log)
    worker_spans = span_names(worker_log)

    checks.append((
        "the synchronous path exported spans from the API",
        any("transcriptions" in name for name in api_spans),
        ", ".join(sorted(api_spans)[:8]) or "none",
    ))
    checks.append((
        "the asynchronous path exported spans from the worker",
        bool(worker_spans),
        ", ".join(sorted(worker_spans)[:8]) or "none",
    ))

    def trace_ids(text: str) -> set[str]:
        found = set(re.findall(r'"trace_id":\s*"(?:0x)?([0-9a-f]{32})"', text))
        return {t for t in found if t.strip("0")}  # drop the all-zero invalid id

    traces_api = trace_ids(api_log)
    traces_worker = trace_ids(worker_log)
    shared = traces_api & traces_worker
    checks.append((
        "trace ids are present in both processes",
        bool(traces_api) and bool(traces_worker),
        f"api {len(traces_api)} trace(s), worker {len(traces_worker)} trace(s)",
    ))
    checks.append((
        "the async job's trace crosses the process boundary (api -> worker)",
        bool(shared),
        f"{len(shared)} shared trace id(s)"
        + (f", e.g. {sorted(shared)[0]}" if shared else ""),
    ))

    # ---- restore the shipped default ---------------------------------------
    set_env("STT_OTEL_EXPORTER", "otlp")
    restart_api_worker()

    summary = {
        "sample_job": sync_job["id"],
        "async_job": async_job["id"],
        "async_status": async_job["status"],
        "api_spans": sorted(api_spans),
        "worker_spans": sorted(worker_spans),
        "api_trace_count": len(traces_api),
        "worker_trace_count": len(traces_worker),
        "shared_trace_ids": sorted(shared),
        "checks": [
            {"name": name, "ok": ok, "detail": detail} for name, ok, detail in checks
        ],
    }
    (OUT / "summary.json").write_text(
        json.dumps(summary, indent=2) + "\n", encoding="utf-8"
    )

    failed = [name for name, ok, _ in checks if not ok]
    for name, ok, detail in checks:
        print(f"  [{'PASS' if ok else 'FAIL'}] {name} -- {detail}")
    print(f"\n{len(checks) - len(failed)}/{len(checks)} checks passed")
    print(f"evidence: {OUT}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

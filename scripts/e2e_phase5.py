"""Phase 5 end-to-end: the real stack with observability switched on.

Proves the phase's claims with real processes rather than configuration:

1. API and worker run with ``STT_OTEL_ENABLED=true`` (console exporter),
   ``STT_LOG_JSON=true`` and the Prometheus reader enabled;
2. one real job (10 s sample, local GPU provider) goes 202 -> SUCCEEDED;
3. ``/metrics`` serves the recorded counters/histograms/gauges;
4. the API's JSON access log carries the correlation id and the span
   exporter printed a server span for the request;
5. the worker's exported spans show workflow -> activity -> provider in one
   trace, tagged with the job id.

Usage:  uv run python scripts/e2e_phase5.py
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx

REPO = Path(__file__).resolve().parents[1]
BACKEND = REPO / "backend"
SAMPLE = REPO / "audio" / "sample.wav"
RESULTS = REPO / "results" / "phase5_e2e"
API = "http://127.0.0.1:8078"
PROM_PORT = 9466

SPAN_NAME_RE = re.compile(r'"name":\s*"([^"]+)"')
TRACE_ID_RE = re.compile(r'"trace_id":\s*"([0-9a-f]{32})"')
JOB_ID_RE = re.compile(r'"stt\.job_id":\s*"([^"]+)"')


def wait_http(url: str, timeout_s: float) -> None:
    deadline = time.monotonic() + timeout_s
    last: Exception | None = None
    while time.monotonic() < deadline:
        try:
            response = httpx.get(url, timeout=2.0)
            if response.status_code < 500:
                return
        except Exception as exc:  # noqa: BLE001 - retry until the deadline
            last = exc
        time.sleep(0.5)
    raise RuntimeError(f"{url} never became ready: {last!r}")


def poll_job(client: httpx.Client, job_id: str, timeout_s: float) -> dict:
    deadline = time.monotonic() + timeout_s
    seen: list[str] = []
    while time.monotonic() < deadline:
        payload = client.get(f"/v1/transcriptions/{job_id}").json()
        status = payload["status"]
        if not seen or seen[-1] != status:
            seen.append(status)
        if status in {"SUCCEEDED", "FAILED", "CANCELLED"}:
            payload["_trail"] = seen
            return payload
        time.sleep(1.0)
    raise TimeoutError(f"job {job_id} stuck at {seen[-1] if seen else '?'}")


def observability_env() -> dict[str, str]:
    env = dict(os.environ)
    env.update(
        {
            "STT_OTEL_ENABLED": "true",
            "STT_OTEL_EXPORTER": "console",
            "STT_OTEL_ENVIRONMENT": "e2e",
            "STT_LOG_JSON": "true",
            "STT_LOG_LEVEL": "INFO",
            "STT_PROMETHEUS_ENABLED": "true",
            "STT_PROMETHEUS_PORT": str(PROM_PORT),
            "STT_RUNTIME_SAMPLE_INTERVAL_S": "2",
            # Own queue: the shared one carries workflows from earlier runs
            # whose jobs are already terminal, which only adds noise here.
            "STT_TEMPORAL_TASK_QUEUE": "stt-e2e-phase5",
        }
    )
    return env


def start_api(tmp: Path) -> subprocess.Popen:
    log = open(tmp / "api.log", "w")  # noqa: SIM115
    return subprocess.Popen(
        [
            "uv",
            "run",
            "python",
            "-m",
            "uvicorn",
            "stt_platform.main:app",
            "--port",
            "8078",
            "--log-level",
            "warning",
        ],
        cwd=BACKEND,
        stdout=log,
        stderr=subprocess.STDOUT,
        env=observability_env(),
    )


def start_worker(tmp: Path) -> subprocess.Popen:
    log = open(tmp / "worker.log", "w")  # noqa: SIM115
    return subprocess.Popen(
        ["uv", "run", "python", "-m", "stt_platform.workers.transcription_worker"],
        cwd=BACKEND,
        stdout=log,
        stderr=subprocess.STDOUT,
        env=observability_env(),
    )


def kill_tree(proc: subprocess.Popen | None) -> None:
    if proc is None or proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def _metric_value(text: str, metric: str, labels: str = "") -> float | None:
    """Value of ``metric{labels}`` in the Prometheus text exposition."""
    pattern = re.compile(
        rf"^{re.escape(metric)}\{{{re.escape(labels)}\}}\s+([0-9.eE+]+)$",
        re.MULTILINE,
    )
    match = pattern.search(text)
    return float(match.group(1)) if match else None


def _metric_present(text: str, metric: str) -> bool:
    return re.search(rf"^{re.escape(metric)}(?:\{{|\s)", text, re.MULTILINE) is not None


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="stt-e2e5-"))
    results: dict = {
        "phase": 5,
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "api": API,
    }
    api_proc = worker_proc = None
    try:
        assert SAMPLE.is_file(), f"missing sample audio: {SAMPLE}"
        results["audio"] = {
            "path": str(SAMPLE.relative_to(REPO)),
            "size_bytes": SAMPLE.stat().st_size,
        }

        print("[e2e] starting API (otel=console, prometheus=on, json logs) ...")
        api_proc = start_api(tmp)
        wait_http(f"{API}/v1/health/live", 90)
        print("[e2e] API ready")

        print("[e2e] starting Temporal worker ...")
        worker_proc = start_worker(tmp)
        time.sleep(12)

        client = httpx.Client(base_url=API, timeout=300)
        with open(SAMPLE, "rb") as fh:
            response = client.post(
                "/v1/transcriptions",
                files={"file": (SAMPLE.name, fh, "audio/wav")},
                headers={"X-Request-ID": "e2e-phase5"},
            )
        assert response.status_code in (200, 201, 202), response.text
        job_id = response.json()["id"]
        print(f"[e2e] job {job_id} accepted ({response.status_code})")
        results["job_id"] = job_id

        final = poll_job(client, job_id, timeout_s=420)
        print(f"[e2e] job reached {final['status']} via {' -> '.join(final['_trail'])}")
        results["status"] = final["status"]
        results["trail"] = final["_trail"]
        result = client.get(f"/v1/transcriptions/{job_id}/result").json()
        results["result"] = {
            "provider": result.get("provider"),
            "model": result.get("model"),
            "language": result.get("language"),
            "segments": len(result.get("segments", [])),
            "words": sum(len(s.get("words", [])) for s in result.get("segments", [])),
            "audio_duration_s": result.get("audio_duration_s"),
            "processing_duration_s": result.get("processing_duration_s"),
        }
        assert final["status"] == "SUCCEEDED", final

        # Let the batch span processor flush before the logs are read.
        print("[e2e] waiting for span flush ...")
        time.sleep(8)

        # ------------------------------------------------------ metrics --
        metrics_text = client.get("/metrics").text
        (RESULTS).mkdir(parents=True, exist_ok=True)
        (RESULTS / "metrics.txt").write_text(metrics_text, encoding="utf-8")
        checks = {
            "stt_api_requests_total": _metric_value(
                metrics_text, "stt_api_requests_total", 'http_route="/v1/transcriptions",http_request_method="POST",http_response_status_code="202"'
            ),
            "stt_api_errors_total_present": _metric_present(
                metrics_text, "stt_api_errors_total"
            ),
            "stt_upload_bytes_count": _metric_value(
                metrics_text, "stt_upload_bytes_count"
            ),
            "stt_job_end_to_end_succeeded": _metric_value(
                metrics_text,
                "stt_job_end_to_end_seconds_count",
                'provider="faster_whisper",status="SUCCEEDED"',
            ),
            "stt_job_processing_count": _metric_value(
                metrics_text, "stt_job_processing_seconds_count"
            ),
            "stt_job_real_time_factor_count": _metric_value(
                metrics_text, "stt_job_real_time_factor_count"
            ),
            "stt_provider_calls_ok": _metric_value(
                metrics_text,
                "stt_provider_calls_total",
                'operation="transcribe",outcome="ok",provider="faster_whisper"',
            ),
            "stt_queue_depth": _metric_value(metrics_text, "stt_queue_depth"),
            "stt_process_cpu_percent": _metric_value(
                metrics_text, "stt_process_cpu_percent"
            ),
            "stt_process_memory_bytes": _metric_value(
                metrics_text, "stt_process_memory_bytes"
            ),
        }
        results["metrics"] = checks
        for name, value in checks.items():
            print(f"[e2e] metric {name} = {value!r}")
        missing = [name for name, value in checks.items() if value is None]
        assert not missing, f"metrics missing from /metrics: {missing}"
        assert checks["stt_job_end_to_end_succeeded"] >= 1
        assert checks["stt_job_real_time_factor_count"] >= 1
        assert checks["stt_queue_depth"] >= 0
        assert checks["stt_process_memory_bytes"] > 0

        # --------------------------------------------------------- logs ----
        api_log = _read(tmp / "api.log")
        (RESULTS / "api.log").write_text(api_log, encoding="utf-8")
        access_lines = [
            line
            for line in api_log.splitlines()
            if '"logger": "stt_platform.access"' in line
        ]
        parsed_access = [json.loads(line) for line in access_lines]
        accepted = [
            entry
            for entry in parsed_access
            if entry.get("path") == "/v1/transcriptions"
            and entry.get("status_code") == 202
        ]
        assert accepted, "no JSON access log line for the accepted upload"
        entry = accepted[0]
        assert entry["correlation_id"] == "e2e-phase5"
        assert entry["service"] == "stt-platform"
        assert entry["duration_ms"] >= 0
        results["api_log"] = {
            "json_lines": len(access_lines),
            "correlation_id": entry["correlation_id"],
            "trace_id": entry.get("trace_id"),
            "request_duration_ms": entry["duration_ms"],
        }
        print(
            f"[e2e] access log: corr={entry['correlation_id']} "
            f"trace={entry.get('trace_id')} {entry['duration_ms']}ms"
        )

        # -------------------------------------------------------- spans ----
        worker_log = _read(tmp / "worker.log")
        (RESULTS / "worker.log").write_text(worker_log, encoding="utf-8")
        span_names = set(SPAN_NAME_RE.findall(api_log + worker_log))
        results["spans"] = sorted(span_names)
        for expected in (
            "POST /v1/transcriptions",
            "GET /v1/transcriptions/{job_id}",
        ):
            assert expected in span_names, f"no server span for {expected}: {sorted(span_names)}"

        workflow_spans = [n for n in span_names if "Workflow" in n]
        activity_spans = [n for n in span_names if "Activity" in n]
        domain_spans = [n for n in span_names if n.startswith("stt.")]
        assert workflow_spans, f"no workflow span: {sorted(span_names)}"
        assert activity_spans, f"no activity span: {sorted(span_names)}"
        assert "stt.attempt" in domain_spans, sorted(span_names)
        assert "stt.provider.transcribe" in domain_spans, sorted(span_names)
        results["worker_spans"] = {
            "workflow": sorted(workflow_spans),
            "activity": sorted(activity_spans),
            "domain": sorted(domain_spans),
        }

        # The worker's trace must be shared by workflow, activity and the
        # domain spans of that activity; the job id must be on them.
        worker_trace_ids = set(TRACE_ID_RE.findall(worker_log))
        job_ids = set(JOB_ID_RE.findall(worker_log))
        assert job_id in job_ids, f"job id not on worker spans: {job_ids}"
        results["worker_trace_ids"] = sorted(worker_trace_ids)
        results["job_id_on_spans"] = job_id in job_ids
        assert len(worker_trace_ids) >= 1
        api_trace_id = entry.get("trace_id")
        results["trace_propagated_api_to_worker"] = (
            api_trace_id in worker_trace_ids if api_trace_id else False
        )
        print(
            f"[e2e] worker traces={len(worker_trace_ids)} "
            f"api->worker propagated={results['trace_propagated_api_to_worker']}"
        )

        # ------------------------------------------------------ redaction --
        # No credential material may appear anywhere the processes wrote.
        env_file = BACKEND / ".env"
        leaks: list[str] = []
        if env_file.is_file():
            for line in env_file.read_text(encoding="utf-8").splitlines():
                stripped = line.strip()
                if stripped.startswith("#") or "=" not in stripped:
                    continue
                name, _, value = stripped.partition("=")
                value = value.strip().strip('"').strip("'")
                if "KEY" in name.upper() and len(value) >= 8:
                    if value in api_log or value in worker_log:
                        leaks.append(name)
        assert not leaks, f"credentials leaked into logs: {leaks}"
        results["redaction"] = {
            "checked": "API keys from backend/.env",
            "leaks": leaks,
        }
        print("[e2e] redaction: no credential material in either log")

        results["passed"] = True
        return 0
    except Exception as exc:  # noqa: BLE001 - recorded, then reported
        results["passed"] = False
        results["error"] = f"{type(exc).__name__}: {exc}"
        print(f"[e2e] FAILED: {results['error']}")
        return 1
    finally:
        kill_tree(worker_proc)
        kill_tree(api_proc)
        RESULTS.mkdir(parents=True, exist_ok=True)
        results["finished_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        (RESULTS / "result.json").write_text(
            json.dumps(results, indent=2), encoding="utf-8"
        )
        print(f"[e2e] results written to {RESULTS / 'result.json'}")
        print(f"[e2e] child logs kept in {tmp}")


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python
"""Phase 7 end-to-end: auth, tenancy, quotas, chaos recovery, deletion, retention.

Runs the real stack — real API, real worker, real Temporal, real RustFS, real
Postgres, the real `large-v3-turbo` model — and then breaks it on purpose:

    python scripts/e2e_phase7.py

The headline step kills the worker *while it is transcribing*, proves the job
was not lost, restarts the worker, and waits for the job to finish on its own.
`STT_ACTIVITY_ATTEMPT_TIMEOUT_S=15` is what makes that bounded and quick; in
production the same knob trades long audio against recovery time.

Evidence lands in results/phase7_e2e/result.json.
"""
from __future__ import annotations

import contextlib
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx

REPO = Path(__file__).resolve().parents[1]
BACKEND = REPO / "backend"
RESULTS = REPO / "results" / "phase7_e2e"
API_PORT = 8078
QUOTA_PORT = 8079
TASK_QUEUE = "stt-e2e-phase7"
ACME, GLOBEX = "key-e2e-acme", "key-e2e-globex"
ATTEMPT_TIMEOUT_S = 15

checks: list[dict] = []


def check(name: str, ok: bool, detail: object = None) -> bool:
    checks.append({"name": name, "ok": bool(ok), "detail": detail})
    mark = "PASS" if ok else "FAIL"
    print(f"  [{mark}] {name}" + (f" — {detail}" if detail else ""))
    return bool(ok)


def base_env() -> dict[str, str]:
    env = dict(os.environ)
    env.update(
        {
            "HF_HUB_DISABLE_SYMLINKS_WARNING": "1",
            "HF_HOME": str(REPO / "models" / "huggingface"),
            "HF_HUB_CACHE": str(REPO / "models" / "huggingface" / "hub"),
            "PYTHONUNBUFFERED": "1",
        }
    )
    return env


def api_env(*, quota: int = 500, port: int = API_PORT) -> dict[str, str]:
    env = base_env()
    env.update(
        {
            "STT_AUTH_ENABLED": "true",
            "STT_API_KEYS": f"acme:{ACME},globex:{GLOBEX}",
            "STT_MAX_REQUESTS_PER_MINUTE": str(quota),
            "STT_MAX_SYNC_DURATION_S": "0",
            "STT_TEMPORAL_TASK_QUEUE": TASK_QUEUE,
            "STT_ACTIVITY_ATTEMPT_TIMEOUT_S": str(ATTEMPT_TIMEOUT_S),
        }
    )
    return env


def worker_env() -> dict[str, str]:
    env = base_env()
    env.update(
        {
            "STT_TEMPORAL_TASK_QUEUE": TASK_QUEUE,
            "STT_ACTIVITY_ATTEMPT_TIMEOUT_S": str(ATTEMPT_TIMEOUT_S),
        }
    )
    return env


def start_api(tmp: Path, *, port: int = API_PORT, quota: int = 500) -> subprocess.Popen:
    log = open(tmp / f"api-{port}.log", "w")  # noqa: SIM115
    return subprocess.Popen(
        [
            "uv", "run", "python", "-m", "uvicorn", "stt_platform.main:app",
            "--port", str(port), "--log-level", "warning",
        ],
        cwd=BACKEND,
        stdout=log,
        stderr=subprocess.STDOUT,
        env=api_env(quota=quota, port=port),
    )


def start_worker(tmp: Path) -> subprocess.Popen:
    log = open(tmp / "worker.log", "a")  # noqa: SIM115
    return subprocess.Popen(
        ["uv", "run", "python", "-m", "stt_platform.workers.transcription_worker"],
        cwd=BACKEND,
        stdout=log,
        stderr=subprocess.STDOUT,
        env=worker_env(),
    )


def worker_pids() -> list[int]:
    """PIDs of live transcription-worker processes (the real python child)."""
    script = (
        "Get-CimInstance Win32_Process | "
        "Where-Object { $_.Name -like 'python*' -and "
        "$_.CommandLine -like '*transcription_worker*' } | "
        "Select-Object -ExpandProperty ProcessId"
    )
    result = subprocess.run(
        ["powershell", "-NoProfile", "-Command", script],
        capture_output=True,
        text=True,
        check=False,
    )
    return [
        int(line.strip())
        for line in result.stdout.splitlines()
        if line.strip().isdigit()
    ]


def kill_worker_and_verify(proc: subprocess.Popen, *, timeout_s: float = 20.0) -> bool:
    """Kill the worker tree and prove no worker process survives.

    ``taskkill /T`` on the ``uv`` wrapper can leave the python child running —
    and a surviving worker would quietly finish the job, making the whole
    chaos test a lie. So: kill, then hunt by command line, then escalate.
    """
    kill_tree(proc)
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        survivors = worker_pids()
        if not survivors:
            return True
        for pid in survivors:
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(pid)],
                capture_output=True,
                check=False,
            )
        time.sleep(1.0)
    return not worker_pids()


def kill_tree(proc: subprocess.Popen | None) -> None:
    """Kill the wrapper *and* the python child it spawned."""
    if proc is None:
        return
    if sys.platform == "win32":
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
            capture_output=True,
            check=False,
        )
    elif proc.poll() is None:
        proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()


def port_free(port: int) -> bool:
    with socket.socket() as probe:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            probe.bind(("127.0.0.1", port))
        except OSError:
            return False
    return True


def tcp_open(host: str, port: int) -> bool:
    with socket.socket() as probe:
        probe.settimeout(2.0)
        return probe.connect_ex((host, port)) == 0


def wait_http(url: str, timeout_s: float, *, headers: dict | None = None) -> None:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            response = httpx.get(url, timeout=5.0, headers=headers or {})
            if response.status_code < 500:
                return
        except (httpx.HTTPError, OSError):
            pass  # not up yet; keep waiting until the deadline
        time.sleep(1.0)
    raise TimeoutError(f"{url} never came up")


def psql(sql: str) -> str:
    result = subprocess.run(
        ["docker", "exec", "stt-postgres", "psql", "-U", "stt", "-d", "stt", "-tAc", sql],
        capture_output=True,
        text=True,
        check=False,
    )
    return result.stdout.strip()


def upload(client: httpx.Client, path: Path, *, key: str | None) -> httpx.Response:
    headers = {"X-API-Key": key} if key else {}
    with path.open("rb") as handle:
        return client.post(
            f"http://127.0.0.1:{API_PORT}/v1/transcriptions",
            files={"file": (path.name, handle, "audio/wav")},
            data={"provider": "faster-whisper"},
            headers=headers,
            timeout=120.0,
        )


def poll_status(client: httpx.Client, job_id: str, *, key: str) -> str:
    response = client.get(
        f"http://127.0.0.1:{API_PORT}/v1/transcriptions/{job_id}",
        headers={"X-API-Key": key},
        timeout=30.0,
    )
    return response.json().get("status", "?")


def make_long_clip(tmp: Path) -> Path:
    """A ~30 s clip: long enough to have a real window to kill the worker."""
    source = REPO / "audio" / "sample.wav"
    target = tmp / "long.wav"
    subprocess.run(
        [
            "ffmpeg", "-y", "-v", "error",
            "-stream_loop", "2", "-i", str(source),
            "-t", "30", "-c", "copy", str(target),
        ],
        check=True,
        capture_output=True,
    )
    return target


def main() -> int:
    tmp = RESULTS / "tmp"
    tmp.mkdir(parents=True, exist_ok=True)
    api: subprocess.Popen | None = None
    quota_api: subprocess.Popen | None = None
    worker: subprocess.Popen | None = None
    client = httpx.Client()

    try:
        print("Phase 7 e2e — hardening")

        # --- preflight -------------------------------------------------
        print("\n[1] preflight")
        check("api port free", port_free(API_PORT), f"port {API_PORT}")
        check("quota port free", port_free(QUOTA_PORT), f"port {QUOTA_PORT}")
        check("postgres reachable", tcp_open("127.0.0.1", 25432))
        check("object storage reachable", tcp_open("127.0.0.1", 29000))
        check("temporal reachable", tcp_open("127.0.0.1", 7233))
        if not all(c["ok"] for c in checks):
            print("\nstack is not ready — bring up infrastructure/ and retry")
            return 1

        clip = make_long_clip(tmp)
        check("30 s clip synthesized", clip.is_file(), f"{clip.stat().st_size} bytes")

        api = start_api(tmp)
        wait_http(
            f"http://127.0.0.1:{API_PORT}/v1/health/ready", 120.0
        )
        check("api is ready", True)

        # --- authentication -------------------------------------------
        print("\n[2] authentication")
        no_key = client.post(
            f"http://127.0.0.1:{API_PORT}/v1/transcriptions",
            files={"file": ("x.wav", b"RIFF")},
            timeout=30.0,
        )
        check("missing key is refused", no_key.status_code == 401, no_key.status_code)
        bad_key = upload(client, clip, key="not-a-key")
        check("unknown key is refused", bad_key.status_code == 401, bad_key.status_code)
        live = client.get(f"http://127.0.0.1:{API_PORT}/v1/health/live", timeout=10.0)
        check("health stays open for probes", live.status_code == 200)

        # --- tenant isolation ------------------------------------------
        print("\n[3] tenant isolation")
        accepted = upload(client, clip, key=ACME)
        check("acme can submit", accepted.status_code == 202, accepted.status_code)
        job_id = accepted.json()["id"]
        check("job starts queued", accepted.json()["status"] == "QUEUED")

        other_read = client.get(
            f"http://127.0.0.1:{API_PORT}/v1/transcriptions/{job_id}",
            headers={"X-API-Key": GLOBEX},
            timeout=30.0,
        )
        check(
            "another tenant cannot read it",
            other_read.status_code == 404,
            other_read.json()["error"]["code"],
        )
        other_delete = client.delete(
            f"http://127.0.0.1:{API_PORT}/v1/transcriptions/{job_id}",
            headers={"X-API-Key": GLOBEX},
            timeout=30.0,
        )
        check("another tenant cannot delete it", other_delete.status_code == 404)

        # --- chaos: kill the worker mid-transcription -------------------
        print("\n[4] chaos — kill the worker while it transcribes")
        worker = start_worker(tmp)
        processing_deadline = time.time() + 180
        status = "QUEUED"
        while time.time() < processing_deadline:
            status = poll_status(client, job_id, key=ACME)
            if status == "PROCESSING":
                break
            if status in ("SUCCEEDED", "FAILED", "CANCELLED"):
                break
            time.sleep(0.5)
        check("job reached PROCESSING", status == "PROCESSING", status)

        killed = kill_worker_and_verify(worker)
        worker = None
        check("worker really died", killed, "no transcription_worker process left")

        status_after = poll_status(client, job_id, key=ACME)
        check(
            "job is not lost by the crash",
            status_after in ("PROCESSING", "QUEUED", "RETRY_WAIT"),
            status_after,
        )

        # With no worker, nothing may advance: the attempt times out and the
        # job must still be unfinished. This is the real "the crash stalled it"
        # evidence — not just that a process disappeared.
        print(f"    waiting out the attempt timeout ({ATTEMPT_TIMEOUT_S}s) with no worker ...")
        time.sleep(ATTEMPT_TIMEOUT_S + 5)
        stalled = poll_status(client, job_id, key=ACME)
        check(
            "no progress while the worker is down",
            stalled not in ("SUCCEEDED", "FAILED", "CANCELLED"),
            stalled,
        )

        worker = start_worker(tmp)
        finished = "?"
        deadline = time.time() + 240
        while time.time() < deadline:
            finished = poll_status(client, job_id, key=ACME)
            if finished in ("SUCCEEDED", "FAILED", "CANCELLED"):
                break
            time.sleep(1.0)
        check("job finished after the worker came back", finished == "SUCCEEDED", finished)

        # The trail is written by the worker, so it can only be inspected now.
        trail = psql(
            "SELECT string_agg(to_status, ' -> ' ORDER BY id) FROM job_transitions "
            f"WHERE job_id = '{job_id}'"
        )
        check("the trail records the recovery", "RETRY_WAIT" in trail, trail)

        result = client.get(
            f"http://127.0.0.1:{API_PORT}/v1/transcriptions/{job_id}/result",
            headers={"X-API-Key": ACME},
            timeout=60.0,
        )
        body = result.json() if result.status_code == 200 else {}
        check(
            "the recovered transcript is real",
            result.status_code == 200 and len(body.get("segments", [])) > 0,
            f"{len(body.get('segments', []))} segments",
        )

        # --- deletion ---------------------------------------------------
        print("\n[5] deletion")
        prefix_rows = psql(
            f"SELECT original_object_key FROM jobs WHERE id = '{job_id}'"
        )
        original_key = prefix_rows or None
        removed = client.delete(
            f"http://127.0.0.1:{API_PORT}/v1/transcriptions/{job_id}",
            headers={"X-API-Key": ACME},
            timeout=60.0,
        )
        check("owner can delete", removed.status_code == 204, removed.status_code)
        gone = client.get(
            f"http://127.0.0.1:{API_PORT}/v1/transcriptions/{job_id}",
            headers={"X-API-Key": ACME},
            timeout=30.0,
        )
        check("deleted job is 404", gone.status_code == 404)
        remaining = psql(f"SELECT count(*) FROM jobs WHERE id = '{job_id}'")
        check("job rows are gone", remaining == "0", f"rows={remaining}")
        if original_key:
            objects = psql(
                "SELECT count(*) FROM jobs WHERE original_object_key = "
                f"'{original_key}'"
            )
            check("original object key is released", objects == "0")

        # --- retention (dry run: this must not delete anything) ---------
        print("\n[6] retention dry run")
        before = psql("SELECT count(*) FROM jobs")
        purge = subprocess.run(
            [
                "uv", "run", "python", str(REPO / "scripts" / "purge_expired.py"),
                "--dry-run", "--older-than-days", "0",
            ],
            cwd=REPO,
            capture_output=True,
            text=True,
            env=base_env(),
            check=False,
        )
        report: dict = {}
        with contextlib.suppress(ValueError, json.JSONDecodeError):
            report = json.loads(purge.stdout[purge.stdout.index("{"):])
        after = psql("SELECT count(*) FROM jobs")
        check(
            "dry run finds candidates",
            report.get("candidate_count", 0) > 0,
            f"{report.get('candidate_count')} candidates",
        )
        check("dry run deletes nothing", before == after, f"{before} -> {after}")

        # --- quota ------------------------------------------------------
        print("\n[7] per-tenant quota")
        quota_api = start_api(tmp, port=QUOTA_PORT, quota=2)
        wait_http(f"http://127.0.0.1:{QUOTA_PORT}/v1/health/ready", 120.0)
        with quota_api_client() as quota_client:
            codes = []
            for _ in range(3):
                response = quota_client.post(
                    f"http://127.0.0.1:{QUOTA_PORT}/v1/transcriptions",
                    files={"file": ("x.wav", b"RIFF....")},
                    headers={"X-API-Key": ACME},
                    timeout=60.0,
                )
                codes.append(response.status_code)
            check("third call is rate limited", 429 in codes, codes)

        # --- report -----------------------------------------------------
        passed = sum(1 for c in checks if c["ok"])
        payload = {
            "phase": 7,
            "passed": passed == len(checks),
            "checks": checks,
            "passed_count": passed,
            "total_count": len(checks),
            "job_id": job_id,
            "transition_trail": trail,
        }
        RESULTS.mkdir(parents=True, exist_ok=True)
        (RESULTS / "result.json").write_text(
            json.dumps(payload, indent=2), encoding="utf-8"
        )
        print(f"\n{passed}/{len(checks)} checks passed")
        print(f"evidence: {RESULTS / 'result.json'}")
        return 0 if passed == len(checks) else 1
    finally:
        kill_tree(worker)
        kill_tree(api)
        kill_tree(quota_api)
        client.close()


def quota_api_client():
    return httpx.Client()


if __name__ == "__main__":
    raise SystemExit(main())

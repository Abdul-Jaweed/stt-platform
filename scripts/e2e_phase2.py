"""Phase 2 end-to-end: real stack, real worker, real audio.

Drives the asynchronous pipeline against the live compose stack:

1. builds a >max-sync-duration wav from ``audio/sample.wav`` (ffmpeg),
2. starts the API (uvicorn) and the Temporal worker as child processes,
3. job A: multipart upload -> 202 -> worker transcribes from S3 -> SUCCEEDED,
4. job B: presigned PUT straight to storage -> create by object_key -> SUCCEEDED,
5. job C: cancel while queued -> CANCELLED,
6. job D: worker killed -> job stays QUEUED (durable) -> worker restarted
   -> job completes (recovery across worker restarts),
7. writes ``results/phase2_e2e/result.json`` and prints a summary.

Run (foreground, from the repo root):

    cd backend && uv run python ../scripts/e2e_phase2.py

Requires: docker compose stack up (postgres/rustfs/temporal), ffmpeg,
CUDA + the cached whisper model.
"""
from __future__ import annotations

import json
import math
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx

REPO = Path(__file__).resolve().parents[1]
BACKEND = REPO / "backend"
SAMPLE = REPO / "audio" / "sample.wav"
RESULTS = REPO / "results" / "phase2_e2e"
API = "http://127.0.0.1:8077"
TARGET_SECONDS = 200.0  # comfortably above the 180s sync cap


def run(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, check=True, **kw)


def probe_duration(path: Path) -> float:
    out = run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(path),
        ]
    ).stdout.strip()
    return float(out)


def build_long_wav(dest: Path) -> None:
    sample_s = probe_duration(SAMPLE)
    loops = max(1, math.ceil(TARGET_SECONDS / sample_s))
    run(
        [
            "ffmpeg",
            "-y",
            "-stream_loop",
            str(loops),
            "-i",
            str(SAMPLE),
            "-t",
            str(TARGET_SECONDS),
            "-ar",
            "16000",
            "-ac",
            "1",
            str(dest),
        ]
    )


def wait_http(url: str, timeout_s: float) -> None:
    deadline = time.time() + timeout_s
    last: Exception | None = None
    while time.time() < deadline:
        try:
            r = httpx.get(url, timeout=2)
            if r.status_code == 200:
                return
        except Exception as exc:  # noqa: BLE001
            last = exc
        time.sleep(1)
    raise RuntimeError(f"{url} never became ready: {last!r}")


def poll_job(client: httpx.Client, job_id: str, timeout_s: float) -> dict:
    deadline = time.time() + timeout_s
    last_status = None
    while time.time() < deadline:
        job = client.get(f"/v1/transcriptions/{job_id}").json()
        last_status = job["status"]
        if last_status in ("SUCCEEDED", "FAILED", "CANCELLED"):
            return job
        time.sleep(2)
    raise RuntimeError(f"job {job_id} still {last_status} after {timeout_s}s")


def trail(job_id: str) -> list[str]:
    """Read the persisted transition trail straight from PostgreSQL."""
    from sqlalchemy import text

    from stt_platform.core.config import get_settings
    from stt_platform.persistence.db import make_engine

    engine = make_engine(get_settings().database_url)
    try:
        with engine.connect() as conn:
            rows = conn.execute(
                text(
                    "select from_status, to_status from job_transitions "
                    "where job_id = :j order by id"
                ),
                {"j": job_id},
            ).all()
        return [f"{a}->{b}" for a, b in rows]
    finally:
        engine.dispose()


def kill_tree(proc: subprocess.Popen) -> None:
    subprocess.run(
        ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
        capture_output=True,
        text=True,
    )


def start_worker(tmp: Path, name: str) -> subprocess.Popen:
    log = open(tmp / name, "w")  # noqa: SIM115
    return subprocess.Popen(
        ["uv", "run", "python", "-m", "stt_platform.workers.transcription_worker"],
        cwd=BACKEND,
        stdout=log,
        stderr=subprocess.STDOUT,
    )


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="stt-e2e2-"))
    results: dict = {"phase": 2, "started_at": time.strftime("%Y-%m-%dT%H:%M:%S")}
    api_proc = worker_proc = None
    try:
        long_wav = tmp / "long.wav"
        print(f"[e2e] building {TARGET_SECONDS}s wav from {SAMPLE.name} ...")
        build_long_wav(long_wav)
        duration = probe_duration(long_wav)
        size = long_wav.stat().st_size
        print(f"[e2e] long.wav: {duration:.1f}s, {size} bytes")
        results["audio"] = {"duration_s": duration, "size_bytes": size}

        print("[e2e] starting API on :8077 ...")
        api_log = open(tmp / "api.log", "w")  # noqa: SIM115
        api_proc = subprocess.Popen(
            [
                "uv",
                "run",
                "python",
                "-m",
                "uvicorn",
                "stt_platform.main:app",
                "--port",
                "8077",
                "--log-level",
                "warning",
            ],
            cwd=BACKEND,
            stdout=api_log,
            stderr=subprocess.STDOUT,
        )
        wait_http(f"{API}/v1/health/live", 60)
        print("[e2e] API ready")

        print("[e2e] starting Temporal worker ...")
        worker_proc = start_worker(tmp, "worker.log")
        # The worker is ready when its Temporal worker has polled the queue;
        # jobs are durable in the outbox regardless, so a short grace is fine.
        time.sleep(12)

        client = httpx.Client(base_url=API, timeout=120)

        # -- job A: multipart -> 202 -> worker -> SUCCEEDED -----------------
        print("[e2e] job A: multipart upload (expect 202) ...")
        with open(long_wav, "rb") as fh:
            r = client.post(
                "/v1/transcriptions",
                files={"file": (long_wav.name, fh, "audio/wav")},
            )
        assert r.status_code == 202, (r.status_code, r.text[:300])
        job_a = r.json()
        assert job_a["status"] == "QUEUED", job_a["status"]
        assert job_a["original_object_key"], "original not preserved"
        print(f"[e2e] job A accepted: {job_a['id']} status={job_a['status']}")
        t0 = time.time()
        job_a = poll_job(client, job_a["id"], 480)
        elapsed = time.time() - t0
        assert job_a["status"] == "SUCCEEDED", job_a.get("failure")
        result_a = client.get(f"/v1/transcriptions/{job_a['id']}/result").json()
        # RTF = processing / audio (the Phase 1 convention); >1 would mean
        # slower than realtime. speedup = its inverse, for humans.
        rtf = result_a["processing_duration_s"] / max(result_a["audio_duration_s"], 1e-9)
        speedup = 1 / max(rtf, 1e-9)
        print(
            f"[e2e] job A SUCCEEDED in {elapsed:.1f}s wall "
            f"(RTF {rtf:.3f}, {speedup:.1f}x realtime, "
            f"{len(result_a['text'].split())} words)"
        )
        assert len(result_a["segments"]) > 0
        results["job_a"] = {
            "status": "SUCCEEDED",
            "wall_s": round(elapsed, 1),
            "rtf": round(rtf, 4),
            "speedup_x": round(speedup, 1),
            "words": len(result_a["text"].split()),
            "segments": len(result_a["segments"]),
            "provider": result_a["provider"],
            "model": result_a["model"],
            "timestamp_granularity": result_a["timestamp_granularity"],
            "trail": trail(job_a["id"]),
        }

        # -- job B: presigned PUT -> create by object_key -> SUCCEEDED ------
        print("[e2e] job B: presigned upload (expect 202) ...")
        p = client.post(
            "/v1/transcriptions/uploads/presign", json={"filename": "long.wav"}
        )
        assert p.status_code == 200, p.text[:300]
        presigned = p.json()
        put = httpx.put(
            presigned["upload_url"],
            content=long_wav.read_bytes(),
            headers={"Content-Type": "audio/wav"},
            timeout=300,
        )
        assert put.status_code == 200, (put.status_code, put.text[:300])
        r = client.post(
            "/v1/transcriptions", data={"object_key": presigned["object_key"]}
        )
        assert r.status_code == 202, (r.status_code, r.text[:300])
        job_b = poll_job(client, r.json()["id"], 480)
        assert job_b["status"] == "SUCCEEDED", job_b.get("failure")
        result_b = client.get(f"/v1/transcriptions/{job_b['id']}/result").json()
        print(f"[e2e] job B SUCCEEDED: {len(result_b['text'].split())} words")
        assert result_b["text"], "empty transcript for speech audio"
        results["job_b"] = {
            "status": "SUCCEEDED",
            "words": len(result_b["text"].split()),
            "object_key": presigned["object_key"],
        }

        # -- job C: cancel while queued -> CANCELLED ------------------------
        print("[e2e] job C: cancel while queued ...")
        with open(long_wav, "rb") as fh:
            r = client.post(
                "/v1/transcriptions",
                files={"file": (long_wav.name, fh, "audio/wav")},
            )
        assert r.status_code == 202, (r.status_code, r.text[:300])
        job_c_id = r.json()["id"]
        c = client.post(f"/v1/transcriptions/{job_c_id}/cancel")
        assert c.status_code == 200, (c.status_code, c.text[:300])
        job_c = poll_job(client, job_c_id, 120)
        assert job_c["status"] == "CANCELLED", job_c
        print(f"[e2e] job C CANCELLED (cancel response said {c.json()['status']})")
        results["job_c"] = {
            "status": "CANCELLED",
            "cancel_response": c.json()["status"],
            "trail": trail(job_c_id),
        }

        # -- job D: worker down -> durable QUEUED -> worker up -> SUCCEEDED --
        print("[e2e] job D: recovery across a worker restart ...")
        assert worker_proc is not None
        kill_tree(worker_proc)
        worker_proc = None
        time.sleep(2)
        with open(long_wav, "rb") as fh:
            r = client.post(
                "/v1/transcriptions",
                files={"file": (long_wav.name, fh, "audio/wav")},
            )
        assert r.status_code == 202, (r.status_code, r.text[:300])
        job_d_id = r.json()["id"]
        # With no worker polling, the job must stay non-terminal.
        observed = []
        for _ in range(6):
            time.sleep(2)
            observed.append(
                client.get(f"/v1/transcriptions/{job_d_id}").json()["status"]
            )
        assert set(observed) <= {"QUEUED", "PROCESSING"}, observed
        print(f"[e2e] job D held at {observed[-1]} while the worker was down")
        worker_proc = start_worker(tmp, "worker2.log")
        time.sleep(12)
        job_d = poll_job(client, job_d_id, 480)
        assert job_d["status"] == "SUCCEEDED", job_d.get("failure")
        print("[e2e] job D SUCCEEDED after the worker restart")
        results["job_d"] = {
            "status_while_worker_down": observed,
            "final_status": "SUCCEEDED",
            "trail": trail(job_d_id),
        }

        results["ok"] = True
    except Exception as exc:  # noqa: BLE001
        results["ok"] = False
        results["error"] = f"{type(exc).__name__}: {exc}"
        import traceback

        traceback.print_exc()
        if api_proc is not None and (tmp / "api.log").exists():
            print("--- api.log tail ---")
            print("\n".join((tmp / "api.log").read_text(errors="replace").splitlines()[-15:]))
        for name in ("worker.log", "worker2.log"):
            if (tmp / name).exists():
                print(f"--- {name} tail ---")
                print("\n".join((tmp / name).read_text(errors="replace").splitlines()[-25:]))
    finally:
        for proc in (worker_proc, api_proc):
            if proc is not None:
                kill_tree(proc)
        RESULTS.mkdir(parents=True, exist_ok=True)
        results["finished_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        (RESULTS / "result.json").write_text(
            json.dumps(results, indent=2), encoding="utf-8"
        )
        print(f"[e2e] results written to {RESULTS / 'result.json'}")
        shutil.rmtree(tmp, ignore_errors=True)
    return 0 if results.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())

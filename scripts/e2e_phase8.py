#!/usr/bin/env python
"""Phase 8 end-to-end: true streaming, and that it agrees with the batch path.

Drives the *console's own* path -- the WebSocket through nginx on :8080 -- with
real speech: a streaming WebM/Opus copy of ``audio/sample.wav`` (what a
browser's MediaRecorder produces), sent as binary frames while the session is
open. Then the same recording goes through ``POST /v1/transcriptions`` as a job
and the two transcripts are compared token by token. Streaming is not a second
transcription engine; it is the same audio through the same model, cut into
utterances instead of a whole file -- so the two must agree.

    cd backend && uv run python ../scripts/e2e_phase8.py

Needs the full stack up (``./launch.sh gpu`` or ``start``) and a rebuild of the
images if the streaming code changed. Evidence lands in
``results/phase8_e2e/result.json``.
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

import httpx

REPO = Path(__file__).resolve().parents[1]
CONSOLE = "http://127.0.0.1:8080"
STREAM_URL = "ws://127.0.0.1:8080/v1/transcriptions/stream"
SAMPLE = REPO / "audio" / "sample.wav"
RESULTS = REPO / "results" / "phase8_e2e"
DB_DSN = "host=127.0.0.1 port=25432 dbname=stt user=stt password=stt"

#: The streamed text is a hypothesis against the whole-file transcript. Some
#: divergence is expected (windows are decoded one at a time, without the
#: context of the neighbours), but clean speech must not come out different.
MAX_WER = 0.20

checks: list[dict] = []


def check(name: str, ok: bool, detail: object = None) -> bool:
    checks.append({"name": name, "ok": bool(ok), "detail": detail})
    mark = "PASS" if ok else "FAIL"
    print(f"  [{mark}] {name}" + (f" -- {detail}" if detail else ""))
    return bool(ok)


def job_count() -> int:
    import psycopg

    with psycopg.connect(DB_DSN) as conn, conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM jobs")
        return int(cur.fetchone()[0])


def streaming_webm() -> bytes:
    """What a browser's recorder produces: one streaming WebM/Opus stream."""
    proc = subprocess.run(
        [
            "ffmpeg", "-v", "error", "-i", str(SAMPLE),
            "-c:a", "libopus", "-b:a", "32k", "-ar", "48000", "-ac", "1",
            "-f", "webm", "-",
        ],
        capture_output=True,
        check=True,
    )
    return proc.stdout


def stream_once(payload: bytes) -> tuple[list[dict], float]:
    """Send the recording over the console's socket; collect every frame."""
    from websockets.exceptions import ConnectionClosed
    from websockets.sync.client import connect

    frames: list[dict] = []
    started = time.perf_counter()
    with connect(STREAM_URL, open_timeout=15, close_timeout=10) as ws:
        ws.send(json.dumps({"type": "start", "language": "en", "input_format": "container"}))
        step = max(1024, len(payload) // 12)
        for offset in range(0, len(payload), step):
            ws.send(payload[offset : offset + step])
            time.sleep(0.02)  # a browser sends as it records, not in one breath
        ws.send(json.dumps({"type": "stop"}))
        while True:
            try:
                frame = json.loads(ws.recv(timeout=120))
            except (TimeoutError, ConnectionClosed):
                break
            frames.append(frame)
            if frame.get("type") == "done":
                break
    return frames, time.perf_counter() - started


def main() -> int:
    RESULTS.mkdir(parents=True, exist_ok=True)
    print(f"phase 8 e2e — console {CONSOLE}")

    # 1. The stack is up and the console proxies the API (the 502 rule).
    with httpx.Client(base_url=CONSOLE, timeout=15) as client:
        ready = client.get("/v1/health/ready")
        check(
            "the console reaches the API through nginx",
            ready.status_code == 200,
            ready.status_code,
        )

        providers = client.get("/v1/providers").json()["providers"]
        local = next((p for p in providers if p["is_local"]), None)
        check("a local provider is registered", local is not None, [p["id"] for p in providers])
        streaming_flag = bool(local and local["capabilities"].get("streaming"))
        print(f"  [info] local provider {local['id']!r} reports streaming={streaming_flag}")

    if not SAMPLE.exists():
        check("the sample recording exists", False, str(SAMPLE))
        RESULTS.joinpath("result.json").write_text(json.dumps(checks, indent=2), encoding="utf-8")
        return 1

    payload = streaming_webm()
    check(
        "the test recording transcodes to a streaming webm",
        len(payload) > 1000,
        f"{len(payload)} bytes",
    )

    # 2. Streaming, and only streaming: the job count must not move.
    before = job_count()
    frames, elapsed = stream_once(payload)
    after = job_count()

    kinds = [f.get("type") for f in frames]
    ready_frame = next((f for f in frames if f.get("type") == "ready"), None)
    finals = [f for f in frames if f.get("type") == "final"]
    done = next((f for f in frames if f.get("type") == "done"), None)
    fatal = [f for f in frames if f.get("type") == "error" and f.get("fatal")]

    check("the session opened", ready_frame is not None, kinds[:3])
    check(
        "it streamed on the local model",
        bool(ready_frame and ready_frame["provider"] == "faster-whisper"),
        ready_frame and f"{ready_frame['provider']}/{ready_frame['model']}",
    )
    check("utterances closed as finals", len(finals) >= 1, kinds)
    check("no fatal error frame", not fatal, fatal)
    streamed_text = (done or {}).get("text") or " ".join(f["text"] for f in finals)
    check("the stream produced text", bool(streamed_text.strip()), streamed_text[:90])
    check("the session ended on the client's stop", bool(done and done["reason"] == "stop"), done)
    check(
        "it heard the whole recording",
        bool(done and abs(done["duration_s"] - 10.0) < 1.0),
        done and done["duration_s"],
    )
    starts = [f["segments"][0]["start"] for f in finals if f.get("segments")]
    check("final timestamps do not go backwards", starts == sorted(starts), starts)
    check("streaming created no job", after == before, f"{before} -> {after}")

    # 3. The same audio as a job: the durable path, unchanged by any of this.
    with httpx.Client(base_url=CONSOLE, timeout=600) as client:
        with open(SAMPLE, "rb") as fh:
            created = client.post(
                "/v1/transcriptions",
                files={"file": ("sample.wav", fh, "audio/wav")},
                data={"language": "en"},
            )
        check("the upload was accepted", created.status_code in (201, 202), created.status_code)
        job = created.json()
        job_id = job["id"]
        status = job["status"]
        deadline = time.time() + 300
        while status in ("QUEUED", "PROCESSING") and time.time() < deadline:
            time.sleep(2)
            status = client.get(f"/v1/transcriptions/{job_id}").json()["status"]
        check("the job succeeded", status == "SUCCEEDED", status)
        result = (
            client.get(f"/v1/transcriptions/{job_id}/result").json()
            if status == "SUCCEEDED"
            else {}
        )
    batch_text = (result.get("text") or "").strip()
    check("the job produced text", bool(batch_text), batch_text[:90])
    check(
        "the job used the local model",
        result.get("provider") == "faster-whisper",
        result.get("provider"),
    )

    # 4. Agreement: one model, two ways of cutting the same audio.
    from stt_platform.evaluation.metrics import edit_counts, normalize, tokens

    reference, hypothesis = tokens(batch_text), tokens(streamed_text)
    counts = edit_counts(reference, hypothesis)
    wer = (counts.errors / len(reference)) if reference else None
    check(
        f"streamed text agrees with the batch transcript (WER <= {MAX_WER})",
        wer is not None and wer <= MAX_WER,
        None if wer is None else f"WER {wer:.3f} ({counts.as_dict()})",
    )
    if wer is not None and wer > 0:
        print(f"  [info] batch:    {batch_text}")
        print(f"  [info] streamed: {streamed_text}")

    payload_out = {
        "checks": checks,
        "batch_text": batch_text,
        "streamed_text": streamed_text,
        "wer": wer,
        "stream_seconds": round(elapsed, 2),
        "stream_frames": kinds,
        "jobs_before": before,
        "jobs_after": after,
        "normalized_reference": normalize(batch_text),
    }
    RESULTS.joinpath("result.json").write_text(json.dumps(payload_out, indent=2), encoding="utf-8")

    failed = [c["name"] for c in checks if not c["ok"]]
    print(f"\n{len(checks) - len(failed)}/{len(checks)} checks passed")
    if failed:
        print("failed: " + "; ".join(failed))
    print(f"evidence: {RESULTS / 'result.json'}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

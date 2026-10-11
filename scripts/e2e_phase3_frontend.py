"""Phase 3 e2e (API half): real backend + real audio, frontend-shaped requests.

Assumes the backend is already running on 127.0.0.1:8200 (started by the
executor) and the compose stack is up. Creates a transcription from
``audio/sample.wav`` exactly as the frontend client does (multipart + 
Idempotency-Key), polls to a terminal status, fetches the result, and checks
the invariants the UI depends on. Writes ``results/phase3_e2e/result.json``.

The UI half (upload through the real built app in a browser) is driven
separately and appended to the same result file.
"""
from __future__ import annotations

import json
import sys
import time
import uuid
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
BASE = "http://127.0.0.1:8200"
SAMPLE = ROOT / "audio" / "sample.wav"
OUT_DIR = ROOT / "results" / "phase3_e2e"


def main() -> int:
    result: dict = {"api_base": BASE, "sample": str(SAMPLE), "checks": {}}
    checks = result["checks"]

    with httpx.Client(base_url=BASE, timeout=120.0) as client:
        health = client.get("/v1/health/ready")
        health.raise_for_status()
        checks["health_ready"] = health.status_code == 200

        providers = client.get("/v1/providers").json()["providers"]
        result["providers"] = [p["id"] for p in providers]
        checks["providers_listed"] = len(providers) >= 1

        with SAMPLE.open("rb") as handle:
            response = client.post(
                "/v1/transcriptions",
                files={"file": (SAMPLE.name, handle, "audio/wav")},
                headers={"Idempotency-Key": str(uuid.uuid4())},
            )
        response.raise_for_status()
        job = response.json()
        result["job"] = {
            "id": job["id"],
            "create_http_status": response.status_code,
            "status": job["status"],
        }

        deadline = time.time() + 300.0
        while (
            job["status"] not in ("SUCCEEDED", "FAILED", "CANCELLED")
            and time.time() < deadline
        ):
            time.sleep(1.0)
            job = client.get(f"/v1/transcriptions/{job['id']}").json()
        result["job"]["status"] = job["status"]
        result["job"]["attempt_count"] = job.get("attempt_count")
        checks["job_succeeded"] = job["status"] == "SUCCEEDED"
        if job["status"] != "SUCCEEDED":
            result["job"]["failure"] = job.get("failure")

        if checks["job_succeeded"]:
            transcript = client.get(
                f"/v1/transcriptions/{job['id']}/result"
            ).json()
            segments = transcript["segments"]
            words = sum(len(s.get("words") or []) for s in segments)
            result["transcript"] = {
                "segments": len(segments),
                "words": words,
                "granularity": transcript["timestamp_granularity"],
                "language": transcript.get("language"),
                "provider": transcript["provider"],
                "model": transcript.get("model"),
                "audio_duration_s": transcript["audio_duration_s"],
                "processing_duration_s": transcript["processing_duration_s"],
                "text_head": transcript["text"][:160],
            }
            checks["segments_present"] = len(segments) > 0
            checks["timestamps_ordered"] = all(
                segment["end"] > segment["start"] for segment in segments
            )
            checks["words_available"] = words > 0

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUT_DIR / "result.json"
    path.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2))
    ok = bool(checks) and all(checks.values())
    print("PHASE3_API_E2E:", "OK" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

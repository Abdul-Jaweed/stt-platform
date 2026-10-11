#!/usr/bin/env python
"""Load the transcriptions API and report real latency/throughput numbers.

    python scripts/load_test.py --requests 12 --concurrency 4 \
        --file audio/sample.wav --json-out results/phase7_load/result.json

Every request is a real transcription against the running API (no mocks): the
point is to measure what the stack actually does under concurrency — queueing
behind the GPU semaphore, provider latency, and any error the pipeline leaks.

Reported: per-request latency percentiles (p50/p95/p99), wall time, achieved
throughput, error count and a breakdown of terminal job states.
"""
from __future__ import annotations

import argparse
import json
import statistics
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

import httpx

DEFAULT_API = "http://127.0.0.1:8078"


@dataclass
class Outcome:
    index: int
    seconds: float = 0.0
    status_code: int = 0
    job_id: str | None = None
    job_status: str | None = None
    error: str | None = None


@dataclass
class LoadReport:
    base_url: str
    file: str
    requests: int
    concurrency: int
    wall_s: float = 0.0
    latencies: list[float] = field(default_factory=list)
    outcomes: list[Outcome] = field(default_factory=list)

    @property
    def failures(self) -> list[Outcome]:
        return [o for o in self.outcomes if o.error or o.status_code >= 400]

    def percentile(self, p: float) -> float:
        if not self.latencies:
            return 0.0
        ordered = sorted(self.latencies)
        index = min(len(ordered) - 1, max(0, round(p / 100 * len(ordered)) - 1))
        return ordered[index]

    def as_dict(self) -> dict:
        states: dict[str, int] = {}
        for outcome in self.outcomes:
            key = outcome.job_status or "UNKNOWN"
            states[key] = states.get(key, 0) + 1
        return {
            "base_url": self.base_url,
            "file": self.file,
            "requests": self.requests,
            "concurrency": self.concurrency,
            "wall_s": round(self.wall_s, 3),
            "throughput_rps": round(self.requests / self.wall_s, 3)
            if self.wall_s
            else None,
            "latency_s": {
                "min": round(min(self.latencies), 3) if self.latencies else None,
                "p50": round(self.percentile(50), 3),
                "p95": round(self.percentile(95), 3),
                "p99": round(self.percentile(99), 3),
                "max": round(max(self.latencies), 3) if self.latencies else None,
                "mean": round(statistics.fmean(self.latencies), 3)
                if self.latencies
                else None,
            },
            "errors": len(self.failures),
            "error_detail": [
                {"index": o.index, "status": o.status_code, "error": o.error}
                for o in self.failures[:10]
            ],
            "job_states": states,
        }


def transcribe_once(
    client: httpx.Client, base_url: str, path: Path, index: int, headers: dict
) -> Outcome:
    outcome = Outcome(index=index)
    started = time.perf_counter()
    try:
        with path.open("rb") as handle:
            response = client.post(
                f"{base_url}/v1/transcriptions",
                files={"file": (path.name, handle, "audio/wav")},
                data={"provider": "faster-whisper"},
                headers=headers,
                timeout=600.0,
            )
        outcome.status_code = response.status_code
        if response.status_code in (200, 201, 202):
            body = response.json()
            outcome.job_id = body.get("id")
            outcome.job_status = body.get("status")
        else:
            outcome.error = response.text[:200]
    except Exception as exc:
        outcome.error = f"{type(exc).__name__}: {exc}"
    outcome.seconds = time.perf_counter() - started
    return outcome


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default=DEFAULT_API)
    parser.add_argument("--file", default="audio/sample.wav")
    parser.add_argument("--requests", type=int, default=12)
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--api-key", default=None)
    parser.add_argument("--json-out", default=None)
    args = parser.parse_args(argv)

    path = Path(args.file)
    if not path.is_file():
        print(json.dumps({"status": "error", "reason": f"no such file: {path}"}))
        return 2

    headers = {"X-API-Key": args.api_key} if args.api_key else {}
    report = LoadReport(
        base_url=args.base_url,
        file=str(path),
        requests=args.requests,
        concurrency=args.concurrency,
    )

    with httpx.Client() as client:
        health = client.get(f"{args.base_url}/v1/health/ready", timeout=10.0)
        if health.status_code != 200:
            print(
                json.dumps(
                    {
                        "status": "error",
                        "reason": f"api not ready: {health.status_code}",
                    }
                )
            )
            return 2

        started = time.perf_counter()
        with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
            futures = [
                pool.submit(
                    transcribe_once, client, args.base_url, path, index, headers
                )
                for index in range(args.requests)
            ]
            report.outcomes = [future.result() for future in futures]
        report.wall_s = time.perf_counter() - started

    report.latencies = [
        outcome.seconds for outcome in report.outcomes if not outcome.error
    ]
    payload = report.as_dict()
    payload["status"] = "ok" if not report.failures else "errors"
    print(json.dumps(payload, indent=2))

    if args.json_out:
        target = Path(args.json_out)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return 0 if not report.failures else 1


if __name__ == "__main__":
    raise SystemExit(main())

"""Phase 6 end-to-end: reference corpus → evaluation → MLflow → read-back.

Real processes, real model, real tracking server:

1. the API runs with MLflow enabled against the compose tracking server;
2. the reference corpus is generated on demand (audio is never committed);
3. ``POST /v1/evaluations`` scores the whole corpus through the real job
   pipeline with ``large-v3-turbo`` on CUDA;
4. the resulting MLflow run is read back from the tracking server: params,
   metrics, tags and the ``result.json`` artifact;
5. ``GET /v1/evaluations`` and ``GET /v1/evaluations/{id}`` agree with the
   record that was created;
6. the error contract is exercised (unknown dataset → 404).

Only the local provider is used: the managed providers are key-gated and a
comparison run against them would spend the owner's free-tier quota.

Usage:  uv run python scripts/e2e_phase6.py
"""
from __future__ import annotations

import contextlib
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx

REPO = Path(__file__).resolve().parents[1]
BACKEND = REPO / "backend"
CORPUS = REPO / "models" / "eval-corpus"
RESULTS = REPO / "results" / "phase6_e2e"
API = "http://127.0.0.1:8078"
MLFLOW = "http://127.0.0.1:5000"
DATASET = "stt-eval-core"
API_PORT = 8078


def log(message: str) -> None:
    print(message, flush=True)


def assert_ports_free(ports: list[int]) -> None:
    """A stale process on the port would silently answer for the new one."""
    busy: list[int] = []
    for port in ports:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(0.5)
            if sock.connect_ex(("127.0.0.1", port)) == 0:
                busy.append(port)
    if busy:
        raise RuntimeError(
            f"port(s) {busy} already in use — kill the stale process first"
        )


def wait_http(url: str, timeout_s: float) -> None:
    deadline = time.monotonic() + timeout_s
    last: Exception | None = None
    while time.monotonic() < deadline:
        try:
            response = httpx.get(url, timeout=2.0)
            if response.status_code < 500:
                return
        except Exception as exc:  # retry until the deadline
            last = exc
        time.sleep(0.5)
    raise RuntimeError(f"{url} never became ready: {last!r}")


def mlflow_is_up() -> bool:
    try:
        return httpx.get(f"{MLFLOW}/health", timeout=3.0).status_code == 200
    except Exception:
        return False


def corpus_is_ready() -> bool:
    manifest = json.loads(
        (
            BACKEND
            / "src"
            / "stt_platform"
            / "evaluation"
            / "datasets"
            / f"{DATASET}.json"
        ).read_text(encoding="utf-8")
    )
    clips = [clip for clip in manifest["clips"] if clip.get("duration_s")]
    return bool(clips) and all((CORPUS / clip["audio"]).is_file() for clip in clips)


def generate_corpus() -> None:
    log("corpus missing — generating it (synthetic speech, ~30 s)…")
    subprocess.run(
        [sys.executable, str(REPO / "scripts" / "make_eval_corpus.py")],
        cwd=BACKEND,
        check=True,
    )


def api_env() -> dict[str, str]:
    env = dict(os.environ)
    env.update(
        {
            "STT_MLFLOW_ENABLED": "true",
            "STT_MLFLOW_TRACKING_URI": MLFLOW,
            "STT_MLFLOW_EXPERIMENT": "stt-evaluation",
            # Warm model + tokenizer from the local cache (no downloads).
            "HF_HUB_DISABLE_SYMLINKS_WARNING": "1",
            "HF_HOME": str(REPO / "models" / "huggingface"),
            "HF_HUB_CACHE": str(REPO / "models" / "huggingface" / "hub"),
        }
    )
    return env


def start_api(tmp: Path) -> tuple[subprocess.Popen, Path]:
    log_path = tmp / "api.log"
    handle = open(log_path, "w")  # noqa: SIM115
    process = subprocess.Popen(
        [
            "uv",
            "run",
            "python",
            "-m",
            "uvicorn",
            "stt_platform.main:app",
            "--port",
            str(API_PORT),
            "--log-level",
            "warning",
        ],
        cwd=BACKEND,
        stdout=handle,
        stderr=subprocess.STDOUT,
        env=api_env(),
    )
    return process, log_path


def kill_tree(process: subprocess.Popen | None) -> None:
    """`uv run` spawns python as a child: kill the tree, not the parent."""
    if process is None or process.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(process.pid)],
            capture_output=True,
            check=False,
        )
    else:
        process.kill()
    with contextlib.suppress(subprocess.TimeoutExpired):
        process.wait(timeout=20)


def key_values(items) -> dict:
    """MLflow's REST API returns params/metrics as [{"key", "value"}] lists."""
    if isinstance(items, list):
        return {item["key"]: item["value"] for item in items}
    return dict(items or {})


def fetch_mlflow_run(run_id: str) -> dict:
    response = httpx.get(
        f"{MLFLOW}/api/2.0/mlflow/runs/get", params={"run_id": run_id}, timeout=20.0
    )
    response.raise_for_status()
    return response.json()["run"]


def fetch_mlflow_artifact(run_id: str) -> dict:
    response = httpx.get(
        f"{MLFLOW}/get-artifact",
        params={"path": "result.json", "run_uuid": run_id},
        timeout=30.0,
    )
    response.raise_for_status()
    return response.json()


def main() -> int:
    RESULTS.mkdir(parents=True, exist_ok=True)
    if not mlflow_is_up():
        log(
            f"MLflow is not reachable at {MLFLOW} — start it first:\n"
            "  cd /i/voice-agent-course/mlflow/docker-compose && "
            "cp .env.dev.example .env && docker compose up -d"
        )
        return 2
    log(f"MLflow up at {MLFLOW}")
    if not corpus_is_ready():
        generate_corpus()
    assert_ports_free([API_PORT])

    evidence: dict = {
        "phase": 6,
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "api": API,
        "mlflow": MLFLOW,
        "dataset": DATASET,
    }
    api_process: subprocess.Popen | None = None
    failures: list[str] = []
    tmp = Path(tempfile.mkdtemp(prefix="stt-e2e6-"))
    log(f"temp dir: {tmp}")
    try:
        api_process, api_log = start_api(tmp)
        wait_http(f"{API}/v1/health/live", timeout_s=90)
        log("API is up")

        client = httpx.Client(base_url=API, timeout=600.0)

        started = time.time()
        response = client.post(
            "/v1/evaluations", json={"dataset_id": DATASET, "language": "en"}
        )
        elapsed = time.time() - started
        evidence["http_status"] = response.status_code
        if response.status_code != 201:
            failures.append(
                f"POST /v1/evaluations -> {response.status_code}: {response.text[:300]}"
            )
            record = {}
        else:
            record = response.json()
        evidence["evaluation_seconds"] = round(elapsed, 2)
        if record:
            aggregate = record["aggregate"]
            evidence.update(
                {
                    "evaluation_id": record["evaluation_id"],
                    "provider": record["provider"],
                    "model": record["model"],
                    "clips_scored": aggregate.get("clips_scored"),
                    "clips_total": aggregate.get("clips_total"),
                    "failures": aggregate.get("failures"),
                    "wer_micro": aggregate.get("wer_micro"),
                    "wer_macro": aggregate.get("wer_macro"),
                    "cer_micro": aggregate.get("cer_micro"),
                    "rtf_mean": aggregate.get("rtf_mean"),
                    "segment_availability_mean": aggregate.get(
                        "segment_availability_mean"
                    ),
                    "word_availability_mean": aggregate.get("word_availability_mean"),
                    "coverage_mean": aggregate.get("coverage_mean"),
                    "boundary_error_mean_s": aggregate.get("boundary_error_mean_s"),
                    "monotonicity_violations_total": aggregate.get(
                        "monotonicity_violations_total"
                    ),
                    "degenerate_spans": aggregate.get("degenerate_spans"),
                    "insufficient_evidence": record["insufficient_evidence"],
                    "normalization": record["normalization"].get("name"),
                    "artifact_key": record["artifact_key"],
                    "mlflow": record["mlflow"],
                    "per_clip": [
                        {
                            "clip_id": clip["clip_id"],
                            "status": clip["status"],
                            "wer": clip["wer"],
                            "cer": clip["cer"],
                            "processing_s": clip["timings"].get("processing_s"),
                            "real_time_factor": clip["timings"].get("real_time_factor"),
                            "audio_sha256": clip["audio_sha256"],
                        }
                        for clip in record["clips"]
                    ],
                }
            )
            log(
                "evaluation: {scored}/{total} clips, WER micro {wer}, CER {cer}, "
                "RTF {rtf}, {secs}s".format(
                    scored=aggregate.get("clips_scored"),
                    total=aggregate.get("clips_total"),
                    wer=aggregate.get("wer_micro"),
                    cer=aggregate.get("cer_micro"),
                    rtf=aggregate.get("rtf_mean"),
                    secs=round(elapsed, 1),
                )
            )
            if aggregate.get("clips_scored") != aggregate.get("clips_total"):
                failures.append("not every clip was scored")
            if aggregate.get("wer_micro") is None:
                failures.append("no WER was computed")
            if aggregate.get("failures"):
                failures.append(f"{aggregate['failures']} clip(s) failed")

            # --- MLflow read-back -------------------------------------------
            mlflow_outcome = record["mlflow"] or {}
            evidence["mlflow_status"] = mlflow_outcome.get("status")
            if mlflow_outcome.get("status") != "logged":
                failures.append(f"MLflow logging did not succeed: {mlflow_outcome}")
            else:
                run = fetch_mlflow_run(mlflow_outcome["run_id"])
                params = key_values(run["data"]["params"])
                metrics = {
                    key: float(value)
                    for key, value in key_values(run["data"]["metrics"]).items()
                }
                tags = key_values(run["data"]["tags"])
                evidence["mlflow_run"] = {
                    "run_id": run["info"]["run_id"],
                    "run_name": run["info"].get("run_name"),
                    "status": run["info"]["status"],
                    "params": params,
                    "wer_micro": metrics.get("wer_micro"),
                    "cer_micro": metrics.get("cer_micro"),
                    "rtf_mean": metrics.get("rtf_mean"),
                    "clips_scored": metrics.get("clips_scored"),
                    "insufficient_evidence": tags.get("insufficient_evidence"),
                }
                if params.get("dataset_id") != DATASET:
                    failures.append("MLflow run does not name the dataset")
                if params.get("normalization") != "stt-normalize-v1":
                    failures.append("MLflow run does not record the normalization")
                if metrics.get("wer_micro") != aggregate.get("wer_micro"):
                    failures.append(
                        "MLflow wer_micro disagrees with the API record "
                        f"({metrics.get('wer_micro')} != {aggregate.get('wer_micro')})"
                    )
                artifact = fetch_mlflow_artifact(mlflow_outcome["run_id"])
                evidence["mlflow_artifact_clips"] = len(artifact.get("clips", []))
                if len(artifact.get("clips", [])) != aggregate.get("clips_scored"):
                    failures.append("MLflow artifact does not hold every clip")
                if not any(
                    clip.get("transcript_text") for clip in artifact.get("clips", [])
                ):
                    failures.append("MLflow artifact holds no transcript text")
                log(
                    f"MLflow run {run['info']['run_id']} verified "
                    f"({len(artifact.get('clips', []))} clips in the artifact)"
                )

            # --- API read-back ----------------------------------------------
            by_id = client.get(f"/v1/evaluations/{record['evaluation_id']}")
            evidence["get_by_id_status"] = by_id.status_code
            if by_id.status_code != 200 or by_id.json() != record:
                failures.append("GET /v1/evaluations/{id} does not match the record")
            listing = client.get("/v1/evaluations")
            evidence["list_status"] = listing.status_code
            rows = listing.json().get("evaluations", [])
            evidence["comparison_rows"] = len(rows)
            matching = [
                row
                for row in rows
                if row["evaluation_id"] == record["evaluation_id"]
            ]
            if not matching:
                failures.append("the new evaluation is missing from the comparison list")
            else:
                evidence["comparison_row"] = matching[0]
                if matching[0]["wer_micro"] != aggregate.get("wer_micro"):
                    failures.append("comparison row WER disagrees with the record")

        # --- error contract --------------------------------------------------
        missing = client.post("/v1/evaluations", json={"dataset_id": "no-such-dataset"})
        evidence["unknown_dataset_status"] = missing.status_code
        evidence["unknown_dataset_code"] = (
            missing.json().get("error", {}).get("code")
        )
        if missing.status_code != 404 or evidence["unknown_dataset_code"] != "DATASET_NOT_FOUND":
            failures.append("unknown dataset did not produce 404 DATASET_NOT_FOUND")

        evidence["api_log_tail"] = api_log.read_text(encoding="utf-8", errors="replace")[-2000:]
    except Exception as exc:
        failures.append(f"{type(exc).__name__}: {exc}")
    finally:
        kill_tree(api_process)

    evidence["failures"] = failures
    evidence["passed"] = not failures
    evidence["finished_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    (RESULTS / "result.json").write_text(
        json.dumps(evidence, indent=2), encoding="utf-8"
    )
    log(f"evidence: {RESULTS / 'result.json'}")
    if failures:
        log("FAILED:")
        for failure in failures:
            log(f"  - {failure}")
        return 1
    log("PASSED: evaluation scored, tracked in MLflow, read back through the API")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

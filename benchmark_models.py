
import csv
import json
import os
import sys
import time
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent

# Configure local caches before importing model libraries.
os.environ["HF_HOME"] = str(PROJECT_DIR / "models" / "huggingface")
os.environ["HF_HUB_CACHE"] = str(PROJECT_DIR / "models" / "huggingface" / "hub")
os.environ["TORCH_HOME"] = str(PROJECT_DIR / ".cache" / "torch")
os.environ["UV_CACHE_DIR"] = str(PROJECT_DIR / ".cache" / "uv")

# Register NVIDIA CUDA DLL directories on Windows.
_DLL_HANDLES = []
if os.name == "nt":
    nvidia_root = (
        Path(sys.prefix) / "Lib" / "site-packages" / "nvidia"
    )
    for folder in ("cublas/bin", "cudnn/bin"):
        dll_dir = nvidia_root / folder
        if dll_dir.is_dir():
            _DLL_HANDLES.append(os.add_dll_directory(str(dll_dir)))
            os.environ["PATH"] = (
                str(dll_dir) + os.pathsep + os.environ.get("PATH", "")
            )

import soundfile as sf
from faster_whisper import WhisperModel

AUDIO = PROJECT_DIR / "audio" / "sample.wav"
MODEL_DIR = PROJECT_DIR / "models" / "faster-whisper"
RESULTS_DIR = PROJECT_DIR / "results"

MODELS = {
    "large-v3-turbo": "large-v3-turbo",
    "distil-large-v3": "Systran/faster-distil-whisper-large-v3",
}


def benchmark(label, model_id):
    print(f"\n{'=' * 60}\nMODEL: {label}\nCheckpoint: {model_id}")

    start = time.perf_counter()
    model = WhisperModel(
        model_id,
        device="cuda",
        compute_type="float16",
        download_root=str(MODEL_DIR),
    )
    load_seconds = time.perf_counter() - start
    print(f"Model load: {load_seconds:.2f}s")

    start = time.perf_counter()
    segments, info = model.transcribe(
        str(AUDIO),
        language="en",
        beam_size=5,
        vad_filter=False,
        word_timestamps=True,
        condition_on_previous_text=False,
    )

    rows = []
    for segment in segments:
        rows.append({
            "start": round(segment.start, 3),
            "end": round(segment.end, 3),
            "text": segment.text.strip(),
            "words": [
                {
                    "start": round(word.start, 3),
                    "end": round(word.end, 3),
                    "text": word.word,
                    "probability": word.probability,
                }
                for word in (segment.words or [])
            ],
        })

    inference_seconds = time.perf_counter() - start
    duration = sf.info(str(AUDIO)).duration
    rtf = inference_seconds / duration if duration else None

    result = {
        "model": label,
        "checkpoint": model_id,
        "audio_duration_seconds": round(duration, 3),
        "load_seconds": round(load_seconds, 3),
        "inference_seconds": round(inference_seconds, 3),
        "rtf": round(rtf, 4) if rtf is not None else None,
        "language": info.language,
        "transcript": " ".join(row["text"] for row in rows).strip(),
        "segments": rows,
    }

    print(f"Transcript: {result['transcript']}")
    print(f"Inference: {inference_seconds:.3f}s")
    print(f"RTF: {rtf:.4f}" if rtf is not None else "RTF: N/A")
    return result


def main():
    if not AUDIO.exists():
        raise FileNotFoundError(f"Missing audio file: {AUDIO}")

    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    results = []

    for label, model_id in MODELS.items():
        try:
            results.append(benchmark(label, model_id))
        except Exception as exc:
            print(f"FAILED: {label}: {type(exc).__name__}: {exc}")

    json_path = RESULTS_DIR / "faster_whisper_benchmark.json"
    json_path.write_text(
        json.dumps(results, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    csv_path = RESULTS_DIR / "faster_whisper_benchmark.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as file:
        fields = [
            "model", "checkpoint", "audio_duration_seconds",
            "load_seconds", "inference_seconds", "rtf", "transcript",
        ]
        writer = csv.DictWriter(file, fieldnames=fields)
        writer.writeheader()
        for result in results:
            writer.writerow({key: result.get(key) for key in fields})

    print(f"\nResults saved under {RESULTS_DIR}")


if __name__ == "__main__":
    main()

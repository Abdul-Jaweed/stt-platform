import json
import os
import time
from pathlib import Path

# Configure local paths before importing model libraries.
PROJECT_DIR = Path(__file__).resolve().parent

os.environ["HF_HOME"] = str(PROJECT_DIR / "models" / "huggingface")
os.environ["HF_HUB_CACHE"] = str(PROJECT_DIR / "models" / "huggingface" / "hub")
os.environ["TORCH_HOME"] = str(PROJECT_DIR / ".cache" / "torch")
os.environ["NEMO_CACHE_DIR"] = str(PROJECT_DIR / ".cache" / "nemo")

import torch
import soundfile as sf
import nemo.collections.asr as nemo_asr

AUDIO_PATH = PROJECT_DIR / "audio" / "sample.wav"
RESULTS_DIR = PROJECT_DIR / "results"
RESULTS_PATH = RESULTS_DIR / "parakeet.json"

MODEL_NAME = "nvidia/parakeet-tdt-0.6b-v2"


def main():
    if not AUDIO_PATH.exists():
        raise FileNotFoundError(f"Audio not found: {AUDIO_PATH}")

    print(f"Audio: {AUDIO_PATH}")
    print(f"PyTorch: {torch.__version__}")
    print(f"CUDA available: {torch.cuda.is_available()}")

    if not torch.cuda.is_available():
        raise RuntimeError("CUDA is unavailable; refusing to benchmark on CPU.")

    print(f"GPU: {torch.cuda.get_device_name(0)}")

    print(f"\nLoading {MODEL_NAME}...")
    load_start = time.perf_counter()

    model = nemo_asr.models.ASRModel.from_pretrained(
        model_name=MODEL_NAME
    )

    # Explicitly move the model to the GPU.
    model = model.cuda()
    model.eval()

    torch.cuda.synchronize()
    load_seconds = time.perf_counter() - load_start

    print(f"Model loaded in {load_seconds:.2f}s")
    print("\nTranscribing audio with timestamps...")

    torch.cuda.reset_peak_memory_stats()
    torch.cuda.synchronize()
    inference_start = time.perf_counter()

    outputs = model.transcribe(
        [str(AUDIO_PATH)],
        timestamps=True,
    )

    torch.cuda.synchronize()
    inference_seconds = time.perf_counter() - inference_start

    hypothesis = outputs[0]
    transcript = getattr(hypothesis, "text", "")
    timestamps = getattr(hypothesis, "timestamp", {}) or {}

    audio_duration = sf.info(str(AUDIO_PATH)).duration
    rtf = inference_seconds / audio_duration if audio_duration else None

    result = {
        "model": MODEL_NAME,
        "audio_file": str(AUDIO_PATH),
        "audio_duration_seconds": round(audio_duration, 3),
        "load_seconds": round(load_seconds, 3),
        "inference_seconds": round(inference_seconds, 3),
        "rtf": round(rtf, 4) if rtf is not None else None,
        "gpu": torch.cuda.get_device_name(0),
        "peak_gpu_memory_mb": round(
            torch.cuda.max_memory_allocated() / (1024 ** 2), 1
        ),
        "transcript": transcript,
        "timestamps": timestamps,
    }

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    RESULTS_PATH.write_text(
        json.dumps(result, indent=2, ensure_ascii=False, default=str),
        encoding="utf-8",
    )

    print("\n" + "=" * 60)
    print("TRANSCRIPT")
    print("=" * 60)
    print(transcript)

    print("\nSEGMENT TIMESTAMPS")
    for segment in timestamps.get("segment", []):
        print(
            f"[{segment.get('start')}s - {segment.get('end')}s] "
            f"{segment.get('segment', '')}"
        )

    print("\nWORD TIMESTAMPS")
    for word in timestamps.get("word", []):
        print(
            f"[{word.get('start')}s - {word.get('end')}s] "
            f"{word.get('word', word.get('text', ''))}"
        )

    print("\nBENCHMARK")
    print(f"Audio duration : {audio_duration:.2f}s")
    print(f"Model load time: {load_seconds:.2f}s")
    print(f"Inference time : {inference_seconds:.2f}s")
    print(f"RTF            : {rtf:.4f}" if rtf is not None else "RTF: N/A")
    print(f"Peak GPU memory: {result['peak_gpu_memory_mb']:.1f} MB")
    print(f"\nSaved results to: {RESULTS_PATH}")


if __name__ == "__main__":
    main()
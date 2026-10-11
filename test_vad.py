
import os
import sys
import time
import logging
from pathlib import Path

# Register CUDA DLL directories before importing Faster-Whisper.
_DLL_HANDLES = []

if os.name == "nt":
    nvidia_root = (
        Path(sys.prefix)
        / "Lib"
        / "site-packages"
        / "nvidia"
    )

    for relative_path in ("cublas/bin", "cudnn/bin"):
        dll_dir = nvidia_root / relative_path

        if dll_dir.is_dir():
            _DLL_HANDLES.append(
                os.add_dll_directory(str(dll_dir))
            )
            os.environ["PATH"] = (
                str(dll_dir)
                + os.pathsep
                + os.environ.get("PATH", "")
            )

from faster_whisper import WhisperModel


MODEL_NAME = "large-v3-turbo"
DEVICE = "cuda"
COMPUTE_TYPE = "float16"


def main():
    if len(sys.argv) > 1:
        audio_path = Path(sys.argv[1])
    else:
        audio_path = Path("audio/sample.wav")

    if not audio_path.is_file():
        raise FileNotFoundError(
            f"Audio file not found: {audio_path}"
        )

    logging.basicConfig(level=logging.WARNING)

    print(f"Audio: {audio_path}")
    print(f"Model: {MODEL_NAME}")
    print(f"Device: {DEVICE} / {COMPUTE_TYPE}")
    print("\nLoading model...")

    model = WhisperModel(
        MODEL_NAME,
        device=DEVICE,
        compute_type=COMPUTE_TYPE,
    )

    results = {}

    for vad_enabled in (True, False):
        print("\n" + "=" * 65)
        print(f"VAD enabled: {vad_enabled}")
        print("=" * 65)

        started = time.perf_counter()

        segments, info = model.transcribe(
            str(audio_path),
            language="en",
            beam_size=5,
            vad_filter=vad_enabled,
            word_timestamps=True,
            condition_on_previous_text=False,
        )

        segment_results = []

        # Inference runs as the generator is consumed.
        for segment in segments:
            words = []

            for word in (segment.words or []):
                words.append({
                    "start": round(word.start, 3),
                    "end": round(word.end, 3),
                    "text": word.word,
                })

            segment_results.append({
                "start": round(segment.start, 3),
                "end": round(segment.end, 3),
                "text": segment.text.strip(),
                "words": words,
            })

            print(
                f"[{segment.start:7.2f}s - "
                f"{segment.end:7.2f}s] "
                f"{segment.text.strip()}"
            )

        elapsed = time.perf_counter() - started
        audio_duration = info.duration
        rtf = (
            elapsed / audio_duration
            if audio_duration
            else None
        )

        results[vad_enabled] = {
            "segments": segment_results,
            "elapsed": elapsed,
            "duration": audio_duration,
            "rtf": rtf,
        }

        print(f"\nLanguage: {info.language}")
        print(f"Audio duration: {audio_duration:.2f}s")
        print(f"Inference time: {elapsed:.2f}s")

        if rtf is not None:
            print(f"RTF: {rtf:.3f}")

        print(
            "Transcript:",
            " ".join(
                item["text"] for item in segment_results
            ),
        )

    print("\n" + "=" * 65)
    print("COMPARISON")
    print("=" * 65)

    for vad_enabled in (True, False):
        result = results[vad_enabled]
        label = "VAD ON " if vad_enabled else "VAD OFF"

        print(
            f"{label} | "
            f"{result['elapsed']:.2f}s inference | "
            f"RTF={result['rtf']:.3f}"
        )

    print("\nCompare the transcripts for missing or added words.")


if __name__ == "__main__":
    main()

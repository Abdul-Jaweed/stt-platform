
import argparse
import io
import json
import logging
import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path

import numpy as np
import sounddevice as sd

# ============================================================
# 1. Windows CUDA DLL setup
# Must happen BEFORE importing faster_whisper / ctranslate2.
# ============================================================

import os
import sys
from pathlib import Path

_DLL_HANDLES = []

if os.name == "nt":
    nvidia_root = (
        Path(sys.prefix)
        / "Lib"
        / "site-packages"
        / "nvidia"
    )

    dll_dirs = [
        nvidia_root / "cublas" / "bin",
        nvidia_root / "cudnn" / "bin",
    ]

    for dll_dir in dll_dirs:
        if dll_dir.is_dir():
            # Register with the Windows DLL loader.
            _DLL_HANDLES.append(
                os.add_dll_directory(str(dll_dir))
            )

            # Also expose the directory through PATH.
            os.environ["PATH"] = (
                str(dll_dir)
                + os.pathsep
                + os.environ.get("PATH", "")
            )

# Import only after registering the DLL directories.
from faster_whisper import WhisperModel



# ============================================================
# 2. Configuration
# ============================================================

SAMPLE_RATE = 16000
MODEL_NAME = "large-v3-turbo"
DEVICE = "cuda"
COMPUTE_TYPE = "float16"

OUTPUT_DIR = Path("output")
DEFAULT_OUTPUT = OUTPUT_DIR / "transcript.json"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)
logger = logging.getLogger("local-stt")

_model = None


# ============================================================
# 3. Model initialization
# ============================================================

def load_model():
    """Load the model once and reuse it across requests."""
    global _model

    if _model is None:
        logger.info(
            "Loading model=%s device=%s compute_type=%s",
            MODEL_NAME,
            DEVICE,
            COMPUTE_TYPE,
        )

        _model = WhisperModel(
            MODEL_NAME,
            device=DEVICE,
            compute_type=COMPUTE_TYPE,
            cpu_threads=4,
        )

        logger.info("Model loaded successfully.")

    return _model


# ============================================================
# 4. Core transcription function
# ============================================================

import time

def transcribe_audio(audio, offset=0.0):
    """Transcribe mono 16 kHz float32 audio and return timestamped results."""
    audio = np.asarray(audio, dtype=np.float32).reshape(-1)

    audio_duration = len(audio) / SAMPLE_RATE
    started_at = time.perf_counter()

    engine = load_model()

    segments, info = engine.transcribe(
        audio,
        language="en",
        beam_size=5,
        vad_filter=True,
        word_timestamps=True,
        condition_on_previous_text=False,
    )

    output_segments = []

    # Consume the generator inside the timer so inference is measured.
    for segment in segments:
        words = []

        for word in (segment.words or []):
            words.append({
                "start": round(offset + word.start, 3),
                "end": round(offset + word.end, 3),
                "text": word.word,
                "probability": getattr(word, "probability", None),
            })

        output_segments.append({
            "start": round(offset + segment.start, 3),
            "end": round(offset + segment.end, 3),
            "text": segment.text.strip(),
            "words": words,
        })

    processing_seconds = time.perf_counter() - started_at

    return {
        "text": " ".join(s["text"] for s in output_segments).strip(),
        "language": info.language,
        "language_probability": getattr(
            info, "language_probability", None
        ),
        "audio_duration": round(audio_duration, 3),
        "processing_seconds": round(processing_seconds, 3),
        "rtf": round(
            processing_seconds / audio_duration, 3
        ) if audio_duration > 0 else None,
        "segments": output_segments,
    }



# ============================================================
# 5. Batch transcription
# ============================================================

def batch_mode(audio_path, output_path):
    """Transcribe a recorded audio file and save JSON output."""
    audio_path = Path(audio_path).expanduser().resolve()
    output_path = Path(output_path).expanduser().resolve()

    if not audio_path.is_file():
        raise FileNotFoundError(
            f"Audio file not found: {audio_path}"
        )

    output_path.parent.mkdir(parents=True, exist_ok=True)

    logger.info("Transcribing file: %s", audio_path)

    result = transcribe_audio(str(audio_path))

    output_path.write_text(
        json.dumps(result, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    print("\n--- TRANSCRIPTION ---")

    for segment in result["segments"]:
        print(
            f'[{segment["start"]:.2f}s - '
            f'{segment["end"]:.2f}s] {segment["text"]}'
        )

    print("\n--- SUMMARY ---")
    print(f'Language: {result["language"]}')
    print(f'Segments: {len(result["segments"])}')
    print(f"Saved JSON: {output_path}")


# ============================================================
# 6. Live microphone transcription
# ============================================================

def live_mode(chunk_seconds=5.0, overlap_seconds=0.8):
    if chunk_seconds <= 0:
        raise ValueError("chunk_seconds must be greater than zero.")

    if not 0 < overlap_seconds < chunk_seconds:
        raise ValueError(
            "overlap_seconds must be greater than 0 "
            "and less than chunk_seconds."
        )

    chunk_samples = int(chunk_seconds * SAMPLE_RATE)
    overlap_samples = int(overlap_seconds * SAMPLE_RATE)
    hop_samples = chunk_samples - overlap_samples
    hop_seconds = hop_samples / SAMPLE_RATE

    previous_tail = None
    window_start = 0.0
    last_emitted_until = 0.0
    first_chunk = True

    print("\nLive transcription started.")
    print(f"Window duration: {chunk_seconds:.1f} seconds")
    print(f"Overlap: {overlap_seconds:.1f} seconds")
    print("Press Ctrl+C to stop.\n")

    try:
        while True:
            # The first capture fills the whole window.
            # Later captures contain only new audio.
            capture_samples = (
                chunk_samples if first_chunk else hop_samples
            )

            recording_started = time.perf_counter()

            new_audio = sd.rec(
                capture_samples,
                samplerate=SAMPLE_RATE,
                channels=1,
                dtype="float32",
            )
            sd.wait()

            new_audio = new_audio.reshape(-1)

            if first_chunk:
                waveform = new_audio
            else:
                waveform = np.concatenate(
                    [previous_tail, new_audio]
                )

            # Preserve the tail for the next overlapping window.
            previous_tail = waveform[-overlap_samples:].copy()

            recording_seconds = (
                time.perf_counter() - recording_started
            )

            result = transcribe_audio(
                waveform,
                offset=window_start,
            )

            # Word-level deduplication:
            # only emit words beginning at or after the last
            # audio boundary already processed.
            emitted_words = []

            for segment in result["segments"]:
                words = segment.get("words") or []

                if words:
                    for word in words:
                        if word["start"] >= last_emitted_until:
                            emitted_words.append(word)
                elif segment["start"] >= last_emitted_until:
                    # Fallback if word timestamps are unavailable.
                    emitted_words.append({
                        "start": segment["start"],
                        "end": segment["end"],
                        "text": segment["text"],
                    })

            if emitted_words:
                text = "".join(
                    word["text"] for word in emitted_words
                ).strip()

                start = emitted_words[0]["start"]
                end = emitted_words[-1]["end"]

                if text:
                    print(f"[{start:.2f}s - {end:.2f}s] {text}")

            processing = result["processing_seconds"]
            rtf = result["rtf"]

            print(
                f"  [metrics] audio={result['audio_duration']:.2f}s "
                f"inference={processing:.2f}s "
                f"RTF={rtf:.3f} "
                f"capture_wait={recording_seconds:.2f}s"
            )

            # The current window covers this point on the timeline.
            last_emitted_until = window_start + chunk_seconds
            window_start += hop_seconds
            first_chunk = False

    except KeyboardInterrupt:
        print("\nLive transcription stopped.")



# ============================================================
# 7. Decode uploaded audio
# ============================================================

def decode_upload(raw_audio):
    """
    Decode audio bytes using PyAV, which faster-whisper uses too.

    Supports formats supported by the installed PyAV/FFmpeg
    build, including common WAV and MP3 files.
    """
    from faster_whisper.audio import decode_audio

    waveform = decode_audio(
        io.BytesIO(raw_audio),
        sampling_rate=SAMPLE_RATE,
    )

    return np.ascontiguousarray(
        waveform,
        dtype=np.float32,
    )


# ============================================================
# 8. FastAPI service
# ============================================================

def api_mode():
    """Start the local HTTP transcription API."""
    import uvicorn
    from fastapi import FastAPI, File, HTTPException, UploadFile

    @asynccontextmanager
    async def lifespan(app):
        load_model()
        yield

    app = FastAPI(
        title="Local STT API",
        description="Offline-first English speech-to-text service",
        version="1.0.0",
        lifespan=lifespan,
    )

    @app.get("/health")
    def health():
        return {
            "status": "ok",
            "model": MODEL_NAME,
            "device": DEVICE,
        }

    @app.post("/transcribe")
    async def transcribe_endpoint(
        file: UploadFile = File(...),
    ):
        # Keep the initial prototype bounded to 100 MB uploads.
        max_bytes = 100 * 1024 * 1024
        raw_audio = await file.read(max_bytes + 1)

        if not raw_audio:
            raise HTTPException(
                status_code=400,
                detail="The uploaded file is empty.",
            )

        if len(raw_audio) > max_bytes:
            raise HTTPException(
                status_code=413,
                detail="Audio file exceeds the 100 MB limit.",
            )

        try:
            waveform = decode_upload(raw_audio)
        except Exception:
            logger.warning(
                "Audio decoding failed for uploaded file %s",
                file.filename,
                exc_info=True,
            )
            raise HTTPException(
                status_code=400,
                detail="Unable to decode this audio file.",
            ) from None

        try:
            return transcribe_audio(waveform)
        except Exception:
            logger.exception("Transcription inference failed")
            raise HTTPException(
                status_code=500,
                detail="Transcription failed. Check the server logs.",
            ) from None

    uvicorn.run(
        app,
        host="127.0.0.1",
        port=8000,
    )


# ============================================================
# 9. Command-line entry point
# ============================================================

def main():
    parser = argparse.ArgumentParser(
        description="Local GPU-accelerated STT pipeline"
    )

    parser.add_argument(
        "mode",
        choices=["batch", "live", "api"],
        help="Execution mode",
    )

    parser.add_argument(
        "--audio",
        help="Input audio file for batch mode",
    )

    parser.add_argument(
        "--output",
        default=str(DEFAULT_OUTPUT),
        help="Output JSON path for batch mode",
    )

    parser.add_argument(
        "--chunk-seconds",
        type=int,
        default=5,
        help="Microphone recording duration per chunk",
    )

    args = parser.parse_args()

    if args.mode == "batch":
        if not args.audio:
            parser.error("--audio is required for batch mode")

        batch_mode(args.audio, args.output)

    elif args.mode == "live":
        live_mode(args.chunk_seconds)

    elif args.mode == "api":
        api_mode()


if __name__ == "__main__":
    main()

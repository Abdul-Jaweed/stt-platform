
import argparse
import io
import json
import time
from pathlib import Path

import numpy as np
import sounddevice as sd
import soundfile as sf
from scipy.signal import resample_poly
from faster_whisper import WhisperModel


SAMPLE_RATE = 16000
MODEL_NAME = "small.en"
DEVICE = "cuda"
COMPUTE_TYPE = "float16"

# One model instance shared by the application.
model = None


def load_model():
    global model

    if model is None:
        print(f"Loading {MODEL_NAME} on {DEVICE}...")
        model = WhisperModel(
            MODEL_NAME,
            device=DEVICE,
            compute_type=COMPUTE_TYPE,
            cpu_threads=4,
        )
        print("Model ready.")

    return model


def transcribe_audio(audio, offset=0.0):
    """Transcribe a file path or 16 kHz mono float32 waveform."""
    engine = load_model()

    segments, info = engine.transcribe(
        audio,
        language="en",
        beam_size=5,
        vad_filter=True,
        condition_on_previous_text=False,
    )

    results = []

    # Iterating executes the actual transcription.
    for segment in segments:
        item = {
            "start": round(offset + segment.start, 3),
            "end": round(offset + segment.end, 3),
            "text": segment.text.strip(),
        }
        results.append(item)

    return {
        "language": info.language,
        "duration": info.duration,
        "segments": results,
    }


def batch_mode(audio_path, output_path):
    result = transcribe_audio(str(audio_path))

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    Path(output_path).write_text(
        json.dumps(result, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    for segment in result["segments"]:
        print(
            f'[{segment["start"]:.2f}s - '
            f'{segment["end"]:.2f}s] {segment["text"]}'
        )

    print(f"Saved transcript to {output_path}")


def live_mode(chunk_seconds=5):
    """Simple chunk-based live microphone transcription."""
    load_model()

    print("Live transcription started. Press Ctrl+C to stop.")
    offset = 0.0

    try:
        while True:
            audio = sd.rec(
                int(chunk_seconds * SAMPLE_RATE),
                samplerate=SAMPLE_RATE,
                channels=1,
                dtype="float32",
            )
            sd.wait()

            waveform = np.asarray(audio[:, 0], dtype=np.float32)

            result = transcribe_audio(waveform, offset=offset)

            for segment in result["segments"]:
                print(
                    f'[{segment["start"]:.2f}s - '
                    f'{segment["end"]:.2f}s] {segment["text"]}',
                    flush=True,
                )

            offset += chunk_seconds

    except KeyboardInterrupt:
        print("\nLive transcription stopped.")


def decode_upload(raw_audio):
    """Decode an uploaded audio file and convert it to 16 kHz mono."""
    audio, sample_rate = sf.read(
        io.BytesIO(raw_audio),
        dtype="float32",
        always_2d=True,
    )

    # Downmix stereo to mono.
    audio = audio.mean(axis=1)

    if sample_rate != SAMPLE_RATE:
        import math

        divisor = math.gcd(sample_rate, SAMPLE_RATE)
        audio = resample_poly(
            audio,
            SAMPLE_RATE // divisor,
            sample_rate // divisor,
        ).astype(np.float32)

    return np.ascontiguousarray(audio, dtype=np.float32)


def api_mode():
    from fastapi import FastAPI, File, UploadFile, HTTPException
    from contextlib import asynccontextmanager
    import uvicorn

    @asynccontextmanager
    async def lifespan(app):
        load_model()
        yield

    app = FastAPI(
        title="Local STT",
        description="Local English speech-to-text service",
        lifespan=lifespan,
    )

    @app.get("/health")
    def health():
        return {"status": "ok", "model": MODEL_NAME, "device": DEVICE}

    @app.post("/transcribe")
    async def transcribe_endpoint(file: UploadFile = File(...)):
        raw = await file.read()

        if not raw:
            raise HTTPException(status_code=400, detail="Empty audio file")

        try:
            waveform = decode_upload(raw)
            return transcribe_audio(waveform)
        except Exception as exc:
            raise HTTPException(
                status_code=400,
                detail=f"Could not transcribe audio: {exc}",
            ) from exc

    uvicorn.run(app, host="127.0.0.1", port=8000)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Local STT pipeline")
    parser.add_argument(
        "mode",
        choices=["batch", "live", "api"],
    )
    parser.add_argument("--audio", help="Path to an audio file")
    parser.add_argument("--output", default="output/transcript.json")
    parser.add_argument("--chunk-seconds", type=int, default=5)

    args = parser.parse_args()

    if args.mode == "batch":
        if not args.audio:
            parser.error("--audio is required for batch mode")
        batch_mode(args.audio, args.output)
    elif args.mode == "live":
        live_mode(args.chunk_seconds)
    else:
        api_mode()

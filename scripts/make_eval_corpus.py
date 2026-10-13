"""Generate the evaluation corpus and fill measured metadata into the manifest.

Audio is never committed (ADR-007): this script recreates it on demand on
the I: drive, then writes the measured ``duration_s``, reference segment
``end`` values and ``sha256`` back into the manifest, so a committed number
can never silently drift from the audio.

Windows speech synthesis (SAPI) speaks each clip's reference text with the
voice named by its ``accent`` field — the text is therefore ground truth by
construction. Noisy clips are mixed from their clean sibling with ffmpeg.

Usage:
    cd backend && uv run python ../scripts/make_eval_corpus.py
    cd backend && uv run python ../scripts/make_eval_corpus.py --check
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import wave
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
BACKEND = REPO / "backend"
sys.path.insert(0, str(BACKEND / "src"))

from stt_platform.evaluation.datasets import (  # noqa: E402
    DatasetError,
    load_dataset,
)

VOICES = {
    "synthetic-us-female": "Microsoft Zira Desktop",
    "synthetic-us-male": "Microsoft David Desktop",
}
REAL_SAMPLE = REPO / "audio" / "sample.wav"
PS_SCRIPT = REPO / "scripts" / "tts_synthesize.ps1"


def wav_duration_s(path: Path) -> float:
    with wave.open(str(path), "rb") as handle:
        return handle.getnframes() / float(handle.getframerate())


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def synthesize(jobs: list[dict]) -> None:
    """Speak every (text, voice, output) job in one PowerShell pass."""
    if not jobs:
        return
    PS_SCRIPT.write_text(_ps_script(jobs), encoding="utf-8")
    result = subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(PS_SCRIPT),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    print(result.stdout.strip())
    if result.returncode != 0:
        raise SystemExit(f"speech synthesis failed:\n{result.stderr}")


def _ps_script(jobs: list[dict]) -> str:
    lines = [
        "Add-Type -AssemblyName System.Speech",
        "$synth = New-Object System.Speech.Synthesis.SpeechSynthesizer",
    ]
    for job in jobs:
        # Rate is re-emitted for every job (0 when the clip has no
        # ``speech_rate``): a fast clip must not leak its rate into the
        # next voice, or the corpus would drift from its manifest.
        lines += [
            f"$synth.Rate = {int(job.get('rate') or 0)}",
            f"$synth.SelectVoice('{job['voice']}')",
            f"$synth.SetOutputToWaveFile('{job['out']}')",
            f"$synth.Speak('{job['text'].replace(chr(39), chr(39) * 2)}')",
        ]
    lines += ["$synth.Dispose()", "Write-Output 'synthesis done'"]
    return "\n".join(lines) + "\n"


def transcode(source: Path, target: Path, *, noise: str | None) -> None:
    """16 kHz mono PCM, optionally mixed with white noise."""
    filters = []
    inputs = ["-i", str(source)]
    if noise:
        amplitude = noise.removeprefix("white-amix-")
        filters = [
            "-f",
            "lavfi",
            "-i",
            f"anoisesrc=color=white:amplitude={amplitude}:"
            f"duration={wav_duration_s(source):.3f}",
            "-filter_complex",
            "[0:a][1:a]amix=inputs=2:duration=first:normalize=0",
        ]
    command = [
        "ffmpeg",
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        *inputs,
        *filters,
        "-ar",
        "16000",
        "-ac",
        "1",
        "-c:a",
        "pcm_s16le",
        str(target),
    ]
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise SystemExit(f"ffmpeg failed for {target.name}:\n{result.stderr}")


def generate(*, check_only: bool) -> int:
    manifest_path = (
        BACKEND
        / "src"
        / "stt_platform"
        / "evaluation"
        / "datasets"
        / "stt-eval-core.json"
    )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    root = REPO / manifest["audio_root"]
    root.mkdir(parents=True, exist_ok=True)

    jobs: list[dict] = []
    plan: list[tuple[dict, Path, str | None]] = []
    for clip in manifest["clips"]:
        target = root / clip["audio"]
        source = clip["reference_source"]
        noise = clip.get("noise")
        if source == "pending-human-verification":
            plan.append((clip, target, None))
        elif noise and noise != "none":
            plan.append((clip, target, noise))
        else:
            voice = VOICES.get(str(clip.get("accent")))
            if voice is None:
                raise SystemExit(f"no voice mapped for accent {clip.get('accent')!r}")
            jobs.append(
                {
                    "voice": voice,
                    "out": str(target.with_suffix(".raw.wav")),
                    "text": clip["reference_text"],
                    "rate": clip.get("speech_rate"),
                }
            )
            plan.append((clip, target, None))

    if check_only:
        missing = [clip["clip_id"] for clip, target, _ in plan if not target.is_file()]
        if missing:
            print(f"missing audio for {len(missing)} clip(s): {', '.join(missing)}")
            return 1
        print(f"all {len(plan)} clips present in {root}")
        return 0

    synthesize(jobs)

    updated = 0
    for clip, target, noise in plan:
        if clip["reference_source"] == "pending-human-verification":
            if not REAL_SAMPLE.is_file():
                raise SystemExit(f"real sample missing: {REAL_SAMPLE}")
            shutil.copyfile(REAL_SAMPLE, target)
        elif noise:
            base = target.name.replace("-noise.wav", ".wav")
            transcode(root / base, target, noise=noise)
        else:
            transcode(target.with_suffix(".raw.wav"), target, noise=None)
            target.with_suffix(".raw.wav").unlink(missing_ok=True)

        duration = round(wav_duration_s(target), 3)
        digest = sha256_file(target)
        if clip.get("duration_s") != duration or clip.get("sha256") != digest:
            clip["duration_s"] = duration
            clip["sha256"] = digest
            updated += 1
        for segment in clip.get("reference_segments") or []:
            if segment.get("end") is None:
                segment["end"] = duration

    manifest_path.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(f"corpus at {root}")
    for clip, target, _ in plan:
        print(f"  {clip['clip_id']:28s} {wav_duration_s(target):6.2f}s  {target.name}")
    print(f"manifest updated ({updated} clip(s) changed): {manifest_path}")

    dataset = load_dataset(manifest_path)
    if dataset.missing_audio:
        raise DatasetError(f"still missing audio: {dataset.missing_audio}")
    print(f"dataset {dataset.dataset_id} v{dataset.version}: "
          f"{len(dataset.scored_clips())} scored clip(s)")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="only verify that the corpus exists",
    )
    args = parser.parse_args()
    return generate(check_only=args.check)


if __name__ == "__main__":
    raise SystemExit(main())

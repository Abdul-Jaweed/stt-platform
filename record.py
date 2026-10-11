
import sounddevice as sd
import soundfile as sf
from pathlib import Path

output = Path("audio/sample.wav")
output.parent.mkdir(parents=True, exist_ok=True)

duration = 10
sample_rate = 16000

print("Speak clearly into your microphone...")
recording = sd.rec(
    int(duration * sample_rate),
    samplerate=sample_rate,
    channels=1,
    dtype="float32",
)

sd.wait()
sf.write(str(output), recording, sample_rate)

print(f"Recording saved: {output.resolve()}")

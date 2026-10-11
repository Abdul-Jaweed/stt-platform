from faster_whisper import WhisperModel

model = WhisperModel(
    "small.en",
    compute_type="float16",
    device="cuda",
)

segments, info = model.transcribe(
    "audio/sample.wav",
    language="en",
    vad_filter=True
)


for segment in segments:
    print(segment.start, segment.end, segment.text)
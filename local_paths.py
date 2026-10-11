# local_paths.py
import os
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent

MODEL_DIR = PROJECT_DIR / "models"
HF_HOME = MODEL_DIR / "huggingface"
FASTER_WHISPER_DIR = MODEL_DIR / "faster-whisper"
WHISPER_CPP_DIR = MODEL_DIR / "whisper.cpp"

CACHE_DIR = PROJECT_DIR / ".cache"

for path in (
    HF_HOME,
    FASTER_WHISPER_DIR,
    WHISPER_CPP_DIR,
    CACHE_DIR / "torch",
    CACHE_DIR / "nemo",
    CACHE_DIR / "uv",
    PROJECT_DIR / "results",
):
    path.mkdir(parents=True, exist_ok=True)

# Hugging Face model downloads and snapshots
os.environ["HF_HOME"] = str(HF_HOME)
os.environ["HF_HUB_CACHE"] = str(HF_HOME / "hub")
os.environ["HUGGINGFACE_HUB_CACHE"] = str(HF_HOME / "hub")
os.environ["TRANSFORMERS_CACHE"] = str(HF_HOME / "transformers")

# Other ML framework caches
os.environ["TORCH_HOME"] = str(CACHE_DIR / "torch")
os.environ["XDG_CACHE_HOME"] = str(CACHE_DIR)

# Keep uv's download/package cache on the project drive too
os.environ["UV_CACHE_DIR"] = str(CACHE_DIR / "uv")
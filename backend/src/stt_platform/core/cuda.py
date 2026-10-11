"""Windows CUDA DLL registration.

ctranslate2 loads cuBLAS/cuDNN at runtime; on Windows the DLL directories
installed by the nvidia-*-cu12 wheels must be registered with the loader
BEFORE the first ctranslate2/faster_whisper import in this process.
Importing this module performs the registration (idempotent).
"""
import os
import sys
from pathlib import Path

_DLL_HANDLES: list = []


def register_cuda_dll_dirs() -> None:
    if os.name != "nt" or _DLL_HANDLES:
        return
    nvidia_root = Path(sys.prefix) / "Lib" / "site-packages" / "nvidia"
    for relative in ("cublas/bin", "cudnn/bin"):
        dll_dir = nvidia_root / relative
        if dll_dir.is_dir():
            _DLL_HANDLES.append(os.add_dll_directory(str(dll_dir)))
            os.environ["PATH"] = (
                str(dll_dir) + os.pathsep + os.environ.get("PATH", "")
            )


register_cuda_dll_dirs()

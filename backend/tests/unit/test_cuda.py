"""CUDA DLL registration is idempotent and effective on Windows."""
import os
import sys
from pathlib import Path

import pytest

from stt_platform.core import cuda


@pytest.mark.skipif(os.name != "nt", reason="Windows only")
def test_cublas_dirs_registered_when_present():
    nvidia_root = Path(sys.prefix) / "Lib" / "site-packages" / "nvidia"
    if not (nvidia_root / "cublas" / "bin").is_dir():
        pytest.skip("nvidia CUDA wheels not installed in this environment")
    path_entries = os.environ["PATH"].split(os.pathsep)
    assert any("nvidia" in p.lower() and "cublas" in p.lower() for p in path_entries)


def test_register_is_idempotent():
    cuda.register_cuda_dll_dirs()
    count = len(cuda._DLL_HANDLES)
    cuda.register_cuda_dll_dirs()
    assert len(cuda._DLL_HANDLES) == count

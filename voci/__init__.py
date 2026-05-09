"""Make CUDA 12 cublas/cudnn libs (installed via pip nvidia-cublas-cu12) findable
to ctranslate2 (which links against libcublas.so.12 / libcudnn.so.9). PyTorch
pulls in cu13 wheels but ctranslate2 needs cu12, so we preload them explicitly.

Only relevant when running the local-STT path; harmless otherwise.
"""
from __future__ import annotations

import ctypes
import sys
from pathlib import Path

_site = (
    Path(sys.prefix)
    / "lib"
    / f"python{sys.version_info.major}.{sys.version_info.minor}"
    / "site-packages"
    / "nvidia"
)

_PRELOAD = [
    "cublas/lib/libcublas.so.12",
    "cublas/lib/libcublasLt.so.12",
    "cudnn/lib/libcudnn.so.9",
    "cudnn/lib/libcudnn_ops.so.9",
    "cudnn/lib/libcudnn_cnn.so.9",
    "cuda_nvrtc/lib/libnvrtc.so.12",
]

for _rel in _PRELOAD:
    _p = _site / _rel
    if _p.exists():
        try:
            ctypes.CDLL(str(_p), mode=ctypes.RTLD_GLOBAL)
        except OSError:
            pass

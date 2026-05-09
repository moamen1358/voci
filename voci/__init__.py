"""Preload CUDA 12 cuBLAS/cuDNN shared libs so CTranslate2 (NLLB translator)
can find them at runtime.

CTranslate2 dlopens ``libcublas.so.12`` and ``libcudnn.so.9``. The
``nvidia-cublas-cu12`` and ``nvidia-cudnn-cu12`` pip packages install these
under ``site-packages/nvidia/...`` but don't add that directory to the dynamic
loader search path. Preloading with ``RTLD_GLOBAL`` makes them resolvable
without LD_LIBRARY_PATH gymnastics. Harmless if the libs are absent.
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

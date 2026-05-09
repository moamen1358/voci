from __future__ import annotations

import logging
import threading
import time
from typing import Any

log = logging.getLogger(__name__)

DEFAULT_MODEL_NAME = "nvidia/parakeet-tdt-0.6b-v2"

_model: Any = None
_model_lock = threading.Lock()


def get_parakeet_model(model_name: str = DEFAULT_MODEL_NAME) -> Any:
    """Process-wide singleton load of the Parakeet TDT RNN-T model.

    Both the streaming (overlay) and session (dictate) facades call this so the
    ~1.2 GB FP16 weights live in CUDA memory exactly once.

    First invocation downloads ~600 MB to ~/.cache/huggingface/. Subsequent
    invocations return the cached instance immediately.
    """
    global _model
    if _model is not None:
        return _model
    with _model_lock:
        if _model is not None:
            return _model
        log.info("Loading Parakeet model %r (first call may download ~600MB)...", model_name)
        t0 = time.monotonic()
        # Imported lazily so importing voci.stt doesn't pay the NeMo import cost
        # until something actually needs the model.
        import torch
        from nemo.collections.asr.models import ASRModel

        model = ASRModel.from_pretrained(model_name=model_name)
        model.eval()
        if torch.cuda.is_available():
            model = model.cuda()
            try:
                # FP16 inference — halves VRAM and roughly doubles throughput on
                # the RTX 4060 with negligible WER impact for this model.
                model = model.half()
            except Exception as e:  # noqa: BLE001
                log.warning("FP16 conversion failed (%s); staying in FP32", e)
        else:
            log.warning("CUDA not available — Parakeet on CPU will be ~10× slower")
        log.info("Parakeet ready in %.1fs", time.monotonic() - t0)
        _model = model
        return _model


def get_target_sample_rate() -> int:
    """Sample rate the loaded Parakeet model expects (16 kHz for v2)."""
    return 16000

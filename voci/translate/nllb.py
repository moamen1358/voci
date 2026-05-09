from __future__ import annotations

import logging
import os
import threading
import time
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

DEFAULT_MODEL_NAME = "facebook/nllb-200-distilled-600M"
CACHE_DIR = Path(os.environ.get("VOCI_CACHE_DIR", Path.home() / ".cache" / "voci"))

# 2-letter ISO → FLORES-200 codes that NLLB-200 uses internally.
_FLORES = {
    "en": "eng_Latn",
    "ar": "arb_Arab",
    "es": "spa_Latn",
    "fr": "fra_Latn",
    "de": "deu_Latn",
    "pt": "por_Latn",
    "it": "ita_Latn",
    "ru": "rus_Cyrl",
    "ja": "jpn_Jpan",
    "zh": "zho_Hans",
    "ko": "kor_Hang",
    "tr": "tur_Latn",
    "nl": "nld_Latn",
    "pl": "pol_Latn",
    "hi": "hin_Deva",
    "id": "ind_Latn",
    "vi": "vie_Latn",
    "th": "tha_Thai",
    "uk": "ukr_Cyrl",
    "fa": "pes_Arab",
}


def _flores(code: str) -> str:
    code = code.lower().strip()
    if code in _FLORES:
        return _FLORES[code]
    if "_" in code and len(code) == 8:
        # Already a FLORES code
        return code
    raise ValueError(
        f"Unsupported language code: {code!r}. Supported 2-letter codes: {sorted(_FLORES)}"
    )


class NllbTranslator:
    """Local NLLB-200 distilled-600M translation via CTranslate2 (FP16, CUDA).

    Drop-in replacement for the deleted ``voci.mymemory_translate.MyMemoryTranslator``
    — same constructor + ``translate(text) -> str`` shape.

    First call lazily:
      1. Downloads the HF source model + tokenizer (~1.2 GB) if not cached.
      2. Converts the model to CTranslate2 format with FP16 quantization
         under ``~/.cache/voci/nllb-ct2/`` (~700 MB on disk after conversion).
      3. Loads the CT2 ``Translator`` onto CUDA.

    Subsequent process restarts skip steps 1-2 and just reload from cache
    (~3 s warm load).
    """

    def __init__(
        self,
        src_lang: str = "en",
        target_lang: str = "ar",
        model_name: str = DEFAULT_MODEL_NAME,
        # ---- compat shims for ex-MyMemory callers (ignored) ----
        email: str | None = None,
        timeout: float | None = None,
    ) -> None:
        self.src_lang = src_lang
        self.target_lang = target_lang
        self.model_name = model_name
        self._translator: Any = None
        self._tokenizer: Any = None
        self._init_lock = threading.Lock()
        log.info("NLLB translator configured (%s -> %s)", src_lang, target_lang)

    def set_pair(self, src: str, tgt: str) -> None:
        # Validate eagerly so a bad code surfaces here, not on the first translate().
        _flores(src)
        _flores(tgt)
        self.src_lang = src
        self.target_lang = tgt

    def translate(self, text: str, *, beam_size: int = 2) -> str:
        text = (text or "").strip()
        if not text:
            return ""
        if self.src_lang == self.target_lang:
            return text
        self._ensure_loaded()

        src_code = _flores(self.src_lang)
        tgt_code = _flores(self.target_lang)

        # NllbTokenizer requires src_lang on the instance to prepend the
        # source-language token correctly.
        self._tokenizer.src_lang = src_code

        encoded = self._tokenizer(text, return_tensors=None)
        source_tokens = self._tokenizer.convert_ids_to_tokens(encoded["input_ids"])

        results = self._translator.translate_batch(
            [source_tokens],
            target_prefix=[[tgt_code]],
            beam_size=beam_size,
            max_decoding_length=256,
        )
        if not results or not results[0].hypotheses:
            return text

        # First token of the hypothesis is the target-lang code; drop it.
        out_tokens = results[0].hypotheses[0][1:]
        out_ids = self._tokenizer.convert_tokens_to_ids(out_tokens)
        translated = self._tokenizer.decode(out_ids, skip_special_tokens=True)
        return translated.strip()

    # --------------------------------------------------------------------
    # Lazy load + one-time HF→CT2 conversion
    # --------------------------------------------------------------------

    def _ensure_loaded(self) -> None:
        if self._translator is not None:
            return
        with self._init_lock:
            if self._translator is not None:
                return
            self._translator, self._tokenizer = _load_or_convert(self.model_name)


# Module-level singletons keep VRAM usage flat when multiple NllbTranslator
# instances are constructed (e.g., overlay + dictate sharing a process).
_translator_singleton: Any = None
_tokenizer_singleton: Any = None
_singleton_lock = threading.Lock()


def _load_or_convert(model_name: str) -> tuple[Any, Any]:
    global _translator_singleton, _tokenizer_singleton
    if _translator_singleton is not None and _tokenizer_singleton is not None:
        return _translator_singleton, _tokenizer_singleton
    with _singleton_lock:
        if _translator_singleton is not None and _tokenizer_singleton is not None:
            return _translator_singleton, _tokenizer_singleton

        import ctranslate2
        import torch
        from transformers import AutoTokenizer

        ct2_dir = CACHE_DIR / "nllb-ct2"

        if not (ct2_dir / "model.bin").exists():
            ct2_dir.mkdir(parents=True, exist_ok=True)
            log.info(
                "Converting %s to CTranslate2 FP16 (one-time, ~3 min, downloads ~1.2 GB)...",
                model_name,
            )
            t0 = time.monotonic()
            from ctranslate2.converters import TransformersConverter

            converter = TransformersConverter(model_name)
            converter.convert(str(ct2_dir), quantization="float16", force=True)
            log.info("CT2 conversion done in %.0fs", time.monotonic() - t0)

        log.info("Loading NLLB tokenizer + CT2 translator...")
        t0 = time.monotonic()
        tokenizer = AutoTokenizer.from_pretrained(model_name)
        device = "cuda" if torch.cuda.is_available() else "cpu"
        compute_type = "float16" if device == "cuda" else "int8"
        translator = ctranslate2.Translator(str(ct2_dir), device=device, compute_type=compute_type)
        log.info(
            "NLLB ready in %.1fs (device=%s compute=%s)",
            time.monotonic() - t0,
            device,
            compute_type,
        )
        _translator_singleton = translator
        _tokenizer_singleton = tokenizer
        return translator, tokenizer

from __future__ import annotations

import logging
import os
import threading
import time
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

CACHE_DIR = Path(os.environ.get("VOCI_CACHE_DIR", Path.home() / ".cache" / "voci"))

# Helsinki-NLP OPUS-MT pairs available as one HF model each. Listed pairs are
# the high-confidence ones for English→X with good quality and small (~80M)
# size. Anything not listed here falls through to NLLB-200 in the factory.
SUPPORTED_PAIRS: dict[tuple[str, str], str] = {
    ("en", "ar"): "Helsinki-NLP/opus-mt-en-ar",
    ("en", "es"): "Helsinki-NLP/opus-mt-en-es",
    ("en", "fr"): "Helsinki-NLP/opus-mt-en-fr",
    ("en", "de"): "Helsinki-NLP/opus-mt-en-de",
    ("en", "it"): "Helsinki-NLP/opus-mt-en-it",
    ("en", "pt"): "Helsinki-NLP/opus-mt-en-roa",  # English → Romance, includes pt
    ("en", "ru"): "Helsinki-NLP/opus-mt-en-ru",
    ("en", "ja"): "Helsinki-NLP/opus-mt-en-jap",
    ("en", "zh"): "Helsinki-NLP/opus-mt-en-zh",
    ("en", "tr"): "Helsinki-NLP/opus-mt-en-trk",  # Turkic family
    ("en", "nl"): "Helsinki-NLP/opus-mt-en-nl",
    ("en", "fa"): "Helsinki-NLP/opus-mt-en-fa",
}


def is_opus_supported(src_lang: str, target_lang: str) -> bool:
    return (src_lang.lower(), target_lang.lower()) in SUPPORTED_PAIRS


class OpusMtTranslator:
    """Helsinki-NLP OPUS-MT translation via CTranslate2 (FP16, CUDA).

    Bilingual MarianMT-architecture models, ~80M params each. ~3-5× faster than
    NLLB-200-distilled-600M at the cost of less general-purpose quality. For
    the pairs listed in ``SUPPORTED_PAIRS`` (notably en→ar) the quality is
    competitive with NLLB and the latency win is the point.

    Same interface shape as ``voci.translate.nllb.NllbTranslator``.

    First call lazily downloads the source model (~300 MB) and converts to CT2
    FP16 under ``~/.cache/voci/opus-mt-<src>-<tgt>/``.
    """

    def __init__(
        self,
        src_lang: str = "en",
        target_lang: str = "ar",
        model_name: str | None = None,
        # ---- compat shims (ignored) ----
        email: str | None = None,
        timeout: float | None = None,
    ) -> None:
        key = (src_lang.lower(), target_lang.lower())
        if model_name is None:
            if key not in SUPPORTED_PAIRS:
                raise ValueError(
                    f"OPUS-MT has no dedicated model for {src_lang}->{target_lang}. "
                    f"Use NllbTranslator instead (see voci.translate factory)."
                )
            model_name = SUPPORTED_PAIRS[key]
        self.src_lang = src_lang
        self.target_lang = target_lang
        self.model_name = model_name
        self._translator: Any = None
        self._tokenizer: Any = None
        self._init_lock = threading.Lock()
        log.info("OPUS-MT translator configured (%s -> %s, %s)", src_lang, target_lang, model_name)

    def set_pair(self, src: str, tgt: str) -> None:
        # OPUS-MT is bilingual per model; changing the pair would require
        # loading a different model. Surface the limitation.
        if (src, tgt) != (self.src_lang, self.target_lang):
            raise NotImplementedError(
                "OPUS-MT translator is bilingual; construct a new instance to switch pairs."
            )

    def translate(self, text: str, *, beam_size: int = 2) -> str:
        text = (text or "").strip()
        if not text:
            return ""
        if self.src_lang == self.target_lang:
            return text
        self._ensure_loaded()

        encoded = self._tokenizer(text, return_tensors=None)
        source_tokens = self._tokenizer.convert_ids_to_tokens(encoded["input_ids"])

        results = self._translator.translate_batch(
            [source_tokens],
            beam_size=beam_size,
            max_decoding_length=256,
        )
        if not results or not results[0].hypotheses:
            return text

        out_tokens = results[0].hypotheses[0]
        out_ids = self._tokenizer.convert_tokens_to_ids(out_tokens)
        return self._tokenizer.decode(out_ids, skip_special_tokens=True).strip()

    # --------------------------------------------------------------------
    # Lazy load + one-time HF→CT2 conversion
    # --------------------------------------------------------------------

    def _ensure_loaded(self) -> None:
        if self._translator is not None:
            return
        with self._init_lock:
            if self._translator is not None:
                return
            self._translator, self._tokenizer = _load_or_convert(
                self.model_name, self.src_lang, self.target_lang
            )


# Each pair gets its own (translator, tokenizer) singleton — keyed by model
# name so different pairs don't collide.
_singletons: dict[str, tuple[Any, Any]] = {}
_singleton_lock = threading.Lock()


def _load_or_convert(model_name: str, src_lang: str, tgt_lang: str) -> tuple[Any, Any]:
    if model_name in _singletons:
        return _singletons[model_name]
    with _singleton_lock:
        if model_name in _singletons:
            return _singletons[model_name]

        import ctranslate2
        import torch
        from transformers import AutoTokenizer

        slug = model_name.split("/")[-1]
        ct2_dir = CACHE_DIR / slug

        if not (ct2_dir / "model.bin").exists():
            ct2_dir.mkdir(parents=True, exist_ok=True)
            log.info(
                "Converting %s to CTranslate2 FP16 (one-time, ~30s, downloads ~300 MB)...",
                model_name,
            )
            t0 = time.monotonic()
            from ctranslate2.converters import TransformersConverter

            converter = TransformersConverter(model_name)
            converter.convert(str(ct2_dir), quantization="float16", force=True)
            log.info("CT2 conversion done in %.0fs", time.monotonic() - t0)

        log.info("Loading OPUS-MT %s tokenizer + CT2 translator...", slug)
        t0 = time.monotonic()
        tokenizer = AutoTokenizer.from_pretrained(model_name)
        device = "cuda" if torch.cuda.is_available() else "cpu"
        compute_type = "float16" if device == "cuda" else "int8"
        translator = ctranslate2.Translator(str(ct2_dir), device=device, compute_type=compute_type)
        log.info(
            "OPUS-MT %s ready in %.1fs (device=%s compute=%s)",
            slug,
            time.monotonic() - t0,
            device,
            compute_type,
        )
        _singletons[model_name] = (translator, tokenizer)
        return translator, tokenizer

"""Translation backends + worker.

Public surface (kept stable for callers):

* ``NllbTranslator(src, tgt, backend='auto')`` — factory that returns the
  best available backend for the language pair:

    backend='auto' (default)
        OPUS-MT (Helsinki-NLP, ~80M params, ~3-5x faster) when a dedicated
        bilingual model exists for the pair, otherwise NLLB-200
        distilled-600M as the universal fallback.
    backend='opus'
        Force OPUS-MT (raises if pair not supported).
    backend='nllb'
        Force NLLB-200 distilled-600M (universal, slower).
    backend='cerebras'
        Cerebras cloud LLM (Llama 3.1 8B by default). 1M tokens/day free,
        ~80-150 ms inference, often better Arabic quality than OPUS-MT
        thanks to LLM context. Requires CEREBRAS_API_KEY.

  All backends expose ``translate(text, *, beam_size=N) -> str``. The
  beam_size argument is ignored by LLM backends.

* ``NllbTranslatorWorker`` — off-thread worker with two submit modes:

    ``submit_final(text, src_lang)`` queues a final translation (serialized,
    no drops, beam=2 for quality).
    ``submit_partial(text, src_lang)`` posts a *replaceable* partial — the
    worker only ever holds the latest one and translates with beam=1 for
    speed. Older pending partials are silently dropped on the floor when
    a newer one arrives.

  ``on_translated(text, src, tgt, is_partial)`` is invoked on the worker
  thread when each translation completes. ``is_partial=True`` means the
  caller should treat it as a provisional rendering that will likely be
  superseded soon.

The legacy ``submit(text, src_lang)`` method is kept as an alias for
``submit_final`` so existing callers (headless mode) still work unchanged.
"""

from __future__ import annotations

from voci.translate._worker import NllbTranslatorWorker
from voci.translate.cerebras_llm import CerebrasLlmTranslator
from voci.translate.nllb import NllbTranslator as _Nllb
from voci.translate.opus_mt import OpusMtTranslator, is_opus_supported


def NllbTranslator(  # noqa: N802 — keep the legacy name as the public factory
    src_lang: str = "en",
    target_lang: str = "ar",
    *,
    backend: str = "auto",
    model: str | None = None,
    **kwargs,
):
    """Factory: returns the requested backend, defaulting to OPUS-MT for
    supported pairs and NLLB-200 otherwise.

    The ``model`` kwarg is honored by the Cerebras backend (overrides the
    default Cerebras model ID); other backends ignore it because OPUS-MT
    and NLLB pick their model from the language pair, not a free-form ID.
    """
    if backend == "cerebras":
        if model:
            kwargs["model"] = model
        return CerebrasLlmTranslator(src_lang=src_lang, target_lang=target_lang, **kwargs)
    if backend == "nllb":
        return _Nllb(src_lang=src_lang, target_lang=target_lang, **kwargs)
    if backend == "opus":
        return OpusMtTranslator(src_lang=src_lang, target_lang=target_lang, **kwargs)
    # backend == 'auto'
    if is_opus_supported(src_lang, target_lang):
        return OpusMtTranslator(src_lang=src_lang, target_lang=target_lang, **kwargs)
    return _Nllb(src_lang=src_lang, target_lang=target_lang, **kwargs)


__all__ = ["NllbTranslator", "NllbTranslatorWorker"]

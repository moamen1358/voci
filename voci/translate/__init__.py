"""Translation backends + worker.

Public surface (kept stable for callers):

* ``NllbTranslator(src, tgt)`` — factory that returns the best available
  backend for the language pair: OPUS-MT (Helsinki-NLP, ~80M params, ~3-5×
  faster) when a dedicated model exists for the pair, otherwise NLLB-200
  distilled-600M as the universal fallback. Both expose
  ``translate(text, *, beam_size=N) -> str``.
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
from voci.translate.nllb import NllbTranslator as _Nllb
from voci.translate.opus_mt import OpusMtTranslator, is_opus_supported


def NllbTranslator(  # noqa: N802 — keep the legacy name as the public factory
    src_lang: str = "en",
    target_lang: str = "ar",
    **kwargs,
):
    """Factory: returns OPUS-MT if available for the pair, else NLLB-200."""
    if is_opus_supported(src_lang, target_lang):
        return OpusMtTranslator(src_lang=src_lang, target_lang=target_lang, **kwargs)
    return _Nllb(src_lang=src_lang, target_lang=target_lang, **kwargs)


__all__ = ["NllbTranslator", "NllbTranslatorWorker"]

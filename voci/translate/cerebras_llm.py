from __future__ import annotations

import logging
import os
import threading
import time
from typing import Any

log = logging.getLogger(__name__)

DEFAULT_MODEL = "llama-3.1-8b"  # 1M tokens/day free, ~80-150 ms inference
TARGET_LANG_NAMES = {
    "ar": "Arabic",
    "en": "English",
    "es": "Spanish",
    "fr": "French",
    "de": "German",
    "it": "Italian",
    "pt": "Portuguese",
    "ru": "Russian",
    "ja": "Japanese",
    "zh": "Chinese (Simplified)",
    "ko": "Korean",
    "tr": "Turkish",
    "nl": "Dutch",
    "pl": "Polish",
    "hi": "Hindi",
    "fa": "Persian",
}


def _lang_name(code: str) -> str:
    code = code.lower().strip()
    return TARGET_LANG_NAMES.get(code, code)


class CerebrasLlmTranslator:
    """Translation via Cerebras Inference API running Llama 3.1 8B (or any
    chat model the account has access to). Free tier in 2026 allows 1M
    tokens/day. Inference latency is ~80-150 ms per call.

    Same constructor + ``translate(text, *, beam_size=N) -> str`` shape as
    the other translators in this package, so the same factory and worker
    can swap it in.

    The ``beam_size`` argument is accepted for interface parity but ignored
    — LLMs sample/decode their own way. Quality is controlled instead via
    ``temperature`` (0 for deterministic translation) and the system prompt.
    """

    def __init__(
        self,
        src_lang: str = "en",
        target_lang: str = "ar",
        model: str = DEFAULT_MODEL,
        api_key: str | None = None,
        # Compat shims for ex-MyMemory callers (ignored)
        email: str | None = None,
        timeout: float | None = None,
    ) -> None:
        self.src_lang = src_lang
        self.target_lang = target_lang
        self.model = model
        self.api_key = api_key or os.environ.get("CEREBRAS_API_KEY")
        if not self.api_key:
            raise RuntimeError(
                "CEREBRAS_API_KEY not set. Get a free key at https://cloud.cerebras.ai/"
            )
        self._client: Any = None
        self._init_lock = threading.Lock()
        log.info(
            "Cerebras LLM translator configured (%s -> %s, model=%s)",
            src_lang,
            target_lang,
            model,
        )

    def set_pair(self, src: str, tgt: str) -> None:
        self.src_lang = src
        self.target_lang = tgt

    def translate(self, text: str, *, beam_size: int = 2) -> str:  # noqa: ARG002
        text = (text or "").strip()
        if not text:
            return ""
        if self.src_lang == self.target_lang:
            return text
        self._ensure_loaded()

        src_name = _lang_name(self.src_lang)
        tgt_name = _lang_name(self.target_lang)

        # System prompt is intentionally strict about output shape — the model
        # tends to add prefatory commentary ("Sure! Here's the translation:")
        # otherwise, which would corrupt our subtitle output. Temperature 0
        # for deterministic, repeatable translations.
        system = (
            f"You are a professional translator. Translate the {src_name} text "
            f"the user gives you into {tgt_name}. Output ONLY the {tgt_name} "
            f"translation. Do not add explanations, quotes, prefixes, or any "
            f"other commentary. If the input is already in {tgt_name}, output "
            f"it unchanged."
        )

        try:
            t0 = time.monotonic()
            resp = self._client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": text},
                ],
                temperature=0.0,
                max_tokens=512,
            )
            dt_ms = (time.monotonic() - t0) * 1000
            translated = (resp.choices[0].message.content or "").strip()
            log.debug("cerebras translate %.0fms: %r -> %r", dt_ms, text[:40], translated[:40])
            return translated
        except Exception as e:  # noqa: BLE001
            log.warning("Cerebras translate failed (%s); returning source text", e)
            return text

    def _ensure_loaded(self) -> None:
        if self._client is not None:
            return
        with self._init_lock:
            if self._client is not None:
                return
            from cerebras.cloud.sdk import Cerebras

            self._client = Cerebras(api_key=self.api_key)
            log.info("Cerebras client ready")

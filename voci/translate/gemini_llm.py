from __future__ import annotations

import logging
import os
import threading
import time
from typing import Any

log = logging.getLogger(__name__)

DEFAULT_MODEL = "gemini-2.5-flash"
# Other Google AI Studio models worth trying via --gemini-model:
#   "gemini-2.5-pro"        — highest quality, slower, more expensive
#   "gemini-2.5-flash-lite" — fastest, cheapest, slightly lower quality
#   "gemini-2.5-flash"      — recommended default; great Arabic, ~200 ms

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
    return TARGET_LANG_NAMES.get(code.lower().strip(), code)


class GeminiLlmTranslator:
    """Translation via Google Gemini API (Google AI Studio).

    Same constructor + ``translate(text, *, beam_size=N) -> str`` shape as
    the other translators in this package, so it slots into ``--translator
    gemini`` through the existing factory and worker.

    Free tier is 10 RPM which is too low for streaming partials; the paid
    tier (~$0.04/hr of subtitles for gemini-2.5-flash) removes the rate
    limit. Inference latency is ~150-300 ms per call.

    The ``beam_size`` argument is accepted for interface parity but ignored
    — LLMs sample/decode their own way. Translation determinism is
    controlled via ``temperature=0`` and a strict system instruction.
    """

    def __init__(
        self,
        src_lang: str = "en",
        target_lang: str = "ar",
        model: str = DEFAULT_MODEL,
        api_key: str | None = None,
        # Compat shims (ignored)
        email: str | None = None,
        timeout: float | None = None,
    ) -> None:
        self.src_lang = src_lang
        self.target_lang = target_lang
        self.model = model
        self.api_key = (
            api_key or os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
        )
        if not self.api_key:
            raise RuntimeError(
                "GEMINI_API_KEY (or GOOGLE_API_KEY) not set. "
                "Get a free key at https://aistudio.google.com/apikey"
            )
        self._client: Any = None
        self._init_lock = threading.Lock()
        log.info(
            "Gemini LLM translator configured (%s -> %s, model=%s)",
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

        # System instruction is intentionally strict about output shape — LLMs
        # tend to add prefatory commentary ("Sure, here's the translation:")
        # otherwise, which would corrupt subtitles. Temperature 0 for
        # deterministic, repeatable translations.
        system_instruction = (
            f"You are a professional translator. Translate the {src_name} text "
            f"the user gives you into {tgt_name}. Output ONLY the {tgt_name} "
            f"translation. Do not add explanations, quotes, prefixes, or any "
            f"other commentary. If the input is already in {tgt_name}, output "
            f"it unchanged."
        )

        try:
            from google.genai.types import GenerateContentConfig

            t0 = time.monotonic()
            resp = self._client.models.generate_content(
                model=self.model,
                contents=text,
                config=GenerateContentConfig(
                    system_instruction=system_instruction,
                    temperature=0.0,
                    max_output_tokens=512,
                ),
            )
            dt_ms = (time.monotonic() - t0) * 1000
            translated = (resp.text or "").strip()
            log.debug("gemini translate %.0fms: %r -> %r", dt_ms, text[:40], translated[:40])
            return translated
        except Exception as e:  # noqa: BLE001
            log.warning("Gemini translate failed (%s); returning source text", e)
            return text

    def _ensure_loaded(self) -> None:
        if self._client is not None:
            return
        with self._init_lock:
            if self._client is not None:
                return
            from google import genai

            self._client = genai.Client(api_key=self.api_key)
            log.info("Gemini client ready")

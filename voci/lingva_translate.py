from __future__ import annotations

import logging
import queue
import threading
import time
from dataclasses import dataclass
from typing import Callable
from urllib.parse import quote

import requests

log = logging.getLogger(__name__)


@dataclass
class TranslationItem:
    text: str
    src_lang: str


TranslatedCallback = Callable[[str, str, str], None]


class LingvaTranslator:
    """Lingva.ml — public Google Translate proxy. Free, no API key, fast.

    Falls back to MyMemory automatically if Lingva is unreachable.
    """

    URL_TEMPLATE = "https://lingva.ml/api/v1/{src}/{tgt}/{text}"

    def __init__(
        self,
        src_lang: str = "en",
        target_lang: str = "ar",
        timeout: float = 4.0,
    ) -> None:
        self.src_lang = src_lang
        self.target_lang = target_lang
        self.timeout = timeout
        self._session = requests.Session()
        # Lazy import + lazy-construct fallback so we don't pay the cost unless Lingva fails
        self._mymemory = None
        log.info("Lingva translator ready (%s -> %s)", src_lang, target_lang)

    def set_pair(self, src: str, tgt: str) -> None:
        self.src_lang = src
        self.target_lang = tgt
        if self._mymemory is not None:
            self._mymemory.set_pair(src, tgt)

    def _translate_lingva(self, text: str) -> str:
        url = self.URL_TEMPLATE.format(
            src=self.src_lang,
            tgt=self.target_lang,
            text=quote(text, safe=""),
        )
        r = self._session.get(url, timeout=self.timeout)
        r.raise_for_status()
        data = r.json()
        out = (data.get("translation") or "").strip()
        if not out:
            raise RuntimeError("empty translation from lingva")
        return out

    def _translate_fallback(self, text: str) -> str:
        if self._mymemory is None:
            from voci.mymemory_translate import MyMemoryTranslator
            self._mymemory = MyMemoryTranslator(src_lang=self.src_lang, target_lang=self.target_lang)
        return self._mymemory.translate(text)

    def translate(self, text: str) -> str:
        text = (text or "").strip()
        if not text:
            return ""
        if self.src_lang == self.target_lang:
            return text
        try:
            return self._translate_lingva(text)
        except Exception as e:
            log.warning("lingva failed (%s), falling back to MyMemory", e)
            try:
                return self._translate_fallback(text)
            except Exception as e2:
                log.error("MyMemory fallback also failed: %s", e2)
                return text


class LingvaTranslatorWorker:
    """Off-thread translation worker."""

    def __init__(
        self,
        translator: LingvaTranslator,
        on_translated: TranslatedCallback,
        max_queue: int = 32,
    ) -> None:
        self.translator = translator
        self.on_translated = on_translated
        self.input_queue: queue.Queue[TranslationItem | None] = queue.Queue(maxsize=max_queue)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def submit(self, text: str, src_lang: str) -> None:
        try:
            self.input_queue.put_nowait(TranslationItem(text=text, src_lang=src_lang))
        except queue.Full:
            try:
                self.input_queue.get_nowait()
            except queue.Empty:
                pass
            self.input_queue.put_nowait(TranslationItem(text=text, src_lang=src_lang))

    def _worker(self) -> None:
        while not self._stop.is_set():
            try:
                item = self.input_queue.get(timeout=0.25)
            except queue.Empty:
                continue
            if item is None:
                return
            try:
                t0 = time.monotonic()
                translated = self.translator.translate(item.text)
                log.debug("translate %.0fms: %r -> %r", (time.monotonic() - t0) * 1000, item.text[:40], translated[:40])
                self.on_translated(translated, item.src_lang, self.translator.target_lang)
            except Exception as e:
                log.exception("translation failed: %s", e)

    def start(self) -> None:
        self._stop.clear()
        self._thread = threading.Thread(target=self._worker, name="lingva-translator", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        try:
            self.input_queue.put_nowait(None)
        except queue.Full:
            pass
        if self._thread is not None:
            self._thread.join(timeout=3.0)

from __future__ import annotations

import logging
import os
import queue
import threading
import time
from dataclasses import dataclass
from typing import Callable

import requests

log = logging.getLogger(__name__)


@dataclass
class TranslationItem:
    text: str
    src_lang: str


TranslatedCallback = Callable[[str, str, str], None]


class MyMemoryTranslator:
    """MyMemory free translation API. 5K chars/day anonymous, 50K with email.

    No signup or API key required. Endpoint: api.mymemory.translated.net.
    """

    URL = "https://api.mymemory.translated.net/get"

    def __init__(
        self,
        src_lang: str = "en",
        target_lang: str = "ar",
        email: str | None = None,  # supplying email raises daily limit to 50K chars
        timeout: float = 5.0,
    ) -> None:
        self.src_lang = src_lang
        self.target_lang = target_lang
        self.email = email or os.environ.get("MYMEMORY_EMAIL")
        self.timeout = timeout
        self._session = requests.Session()
        log.info("MyMemory translator ready (%s -> %s)", src_lang, target_lang)

    def set_pair(self, src: str, tgt: str) -> None:
        self.src_lang = src
        self.target_lang = tgt

    def translate(self, text: str) -> str:
        text = (text or "").strip()
        if not text:
            return ""
        if self.src_lang == self.target_lang:
            return text
        params = {
            "q": text,
            "langpair": f"{self.src_lang}|{self.target_lang}",
        }
        if self.email:
            params["de"] = self.email
        r = self._session.get(self.URL, params=params, timeout=self.timeout)
        r.raise_for_status()
        data = r.json()
        if data.get("responseStatus") not in (200, "200"):
            log.warning("MyMemory error: %s", data.get("responseDetails", "unknown"))
            return text
        return data["responseData"]["translatedText"]


class MyMemoryTranslatorWorker:
    """Run MyMemory translation off the audio/STT thread."""

    def __init__(
        self,
        translator: MyMemoryTranslator,
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
                log.debug("translate %.0fms: %r -> %r", (time.monotonic()-t0)*1000, item.text[:40], translated[:40])
                self.on_translated(translated, item.src_lang, self.translator.target_lang)
            except Exception as e:
                log.exception("translation failed: %s", e)

    def start(self) -> None:
        self._stop.clear()
        self._thread = threading.Thread(target=self._worker, name="mymemory-translator", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        try:
            self.input_queue.put_nowait(None)
        except queue.Full:
            pass
        if self._thread is not None:
            self._thread.join(timeout=3.0)

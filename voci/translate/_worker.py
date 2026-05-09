from __future__ import annotations

import logging
import queue
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

log = logging.getLogger(__name__)

# Callback signature: (translated, src_lang, tgt_lang, is_partial)
TranslatedCallback = Callable[[str, str, str, bool], None]


@dataclass
class _Item:
    text: str
    src_lang: str


class NllbTranslatorWorker:
    """Off-thread translation worker that handles both finals (queued, beam=2)
    and partials (latest-only, beam=1).

    Two submit paths:

    * ``submit_final(text, src)`` — appends to a small bounded queue. Each
      final is translated in order with beam=2 (quality) and the result is
      delivered with ``is_partial=False``.
    * ``submit_partial(text, src)`` — overwrites a single "latest partial"
      slot. The worker drains finals first; whenever no finals are pending,
      it translates the latest partial (if any) with beam=1 (speed). If a
      newer partial arrives while one is in flight, the older in-flight
      result is still delivered (already on the GPU) and the newer one runs
      next; if nothing newer has arrived, the worker waits.

    The legacy ``submit(text, src)`` method maps to ``submit_final``.
    """

    def __init__(
        self,
        translator: Any,
        on_translated: TranslatedCallback,
        max_queue: int = 32,
    ) -> None:
        self.translator = translator
        self.on_translated = on_translated
        self._finals: queue.Queue[_Item | None] = queue.Queue(maxsize=max_queue)

        self._partial_lock = threading.Lock()
        self._partial_pending: _Item | None = None
        self._partial_last_translated: str = ""
        self._wake = threading.Event()

        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # ------------------------------------------------------------------
    # Public submit API
    # ------------------------------------------------------------------

    def submit_final(self, text: str, src_lang: str) -> None:
        try:
            self._finals.put_nowait(_Item(text=text, src_lang=src_lang))
        except queue.Full:
            try:
                self._finals.get_nowait()
            except queue.Empty:
                pass
            self._finals.put_nowait(_Item(text=text, src_lang=src_lang))
        self._wake.set()

    def submit_partial(self, text: str, src_lang: str) -> None:
        with self._partial_lock:
            self._partial_pending = _Item(text=text, src_lang=src_lang)
        self._wake.set()

    # Back-compat: existing callers using submit() always meant a final.
    submit = submit_final

    # ------------------------------------------------------------------
    # Worker
    # ------------------------------------------------------------------

    def _worker(self) -> None:
        while not self._stop.is_set():
            # 1. Drain any pending final (serialized, beam=2). A None entry
            # is a stop() poison pill and exits immediately.
            try:
                item = self._finals.get_nowait()
            except queue.Empty:
                item = None
            else:
                if item is None:
                    return
                self._translate(item, beam_size=2, is_partial=False)
                # A final supersedes any in-flight partial for this utterance.
                with self._partial_lock:
                    self._partial_pending = None
                    self._partial_last_translated = ""
                continue

            # 2. No final pending — try the latest partial (beam=1).
            with self._partial_lock:
                pending = self._partial_pending
                self._partial_pending = None

            if pending is not None and pending.text != self._partial_last_translated:
                self._partial_last_translated = pending.text
                self._translate(pending, beam_size=1, is_partial=True)
                continue

            # 3. Nothing to do — block until woken by submit_*() or stop().
            self._wake.wait(timeout=0.25)
            self._wake.clear()

    def _translate(self, item: _Item, *, beam_size: int, is_partial: bool) -> None:
        try:
            t0 = time.monotonic()
            translated = self.translator.translate(item.text, beam_size=beam_size)
            dt_ms = (time.monotonic() - t0) * 1000
            log.debug(
                "translate%s %.0fms (beam=%d): %r -> %r",
                " (partial)" if is_partial else "",
                dt_ms,
                beam_size,
                item.text[:40],
                translated[:40],
            )
            self.on_translated(translated, item.src_lang, self.translator.target_lang, is_partial)
        except Exception as e:
            log.exception("translation failed: %s", e)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        self._stop.clear()
        self._thread = threading.Thread(target=self._worker, name="translator-worker", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        try:
            self._finals.put_nowait(None)
        except queue.Full:
            pass
        self._wake.set()
        if self._thread is not None:
            self._thread.join(timeout=3.0)

from __future__ import annotations

import argparse
import logging
import os
import signal
import sys
import threading
import time

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtWidgets import QApplication

from voci.audio_capture import AudioCapture
from voci.config import AppConfig
from voci.hotkey import HotkeyListener
from voci.overlay import SubtitleOverlay
from voci.translate import NllbTranslator, NllbTranslatorWorker


class TextRouter(QObject):
    top_line = Signal(str)
    bottom_line = Signal(str)
    clear_lines = Signal()


def setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="voci", description="Real-time audio translator overlay")
    p.add_argument("--monitor-source", help="PulseAudio/PipeWire monitor source name")
    p.add_argument("--target", default="ar", help="Target language for translation (default: ar)")
    p.add_argument("--no-translate", action="store_true", help="English-only, skip translation")
    p.add_argument("--headless", action="store_true", help="No overlay; print to stdout")
    p.add_argument("--show-on-start", action="store_true", help="Show overlay immediately")
    p.add_argument(
        "--stt-backend",
        default="parakeet",
        choices=["parakeet", "deepgram", "soniox", "assemblyai"],
        help="STT backend (default: parakeet local). "
        "'deepgram' = Nova-2 cloud WebSocket (needs DEEPGRAM_API_KEY, $200 free). "
        "'soniox' = sub-200 ms cloud (needs SONIOX_API_KEY, paid). "
        "'assemblyai' = Universal-Streaming v3 cloud with immutable finals "
        "(needs ASSEMBLYAI_API_KEY, $50/~333hr free).",
    )
    p.add_argument(
        "--translator",
        default="auto",
        choices=["auto", "opus", "nllb", "cerebras"],
        help="Translation backend (default: auto = OPUS-MT for supported pairs, "
        "NLLB-200 otherwise). 'cerebras' uses Llama 3.1 8B via Cerebras cloud "
        "(needs CEREBRAS_API_KEY; ~80-150 ms latency, 1 M tokens/day free).",
    )
    p.add_argument("-v", "--verbose", action="store_true")
    return p.parse_args()


def _get_streaming_transcriber_class(backend: str):
    """Lazy-import the right backend so picking parakeet doesn't pay the
    cloud-SDK import cost (and vice versa)."""
    if backend == "deepgram":
        from voci.stt.deepgram import DeepgramStreamingTranscriber

        return DeepgramStreamingTranscriber
    if backend == "soniox":
        from voci.stt.soniox_stt import SonioxStreamingTranscriber

        return SonioxStreamingTranscriber
    if backend == "assemblyai":
        from voci.stt.assemblyai_stt import AssemblyAIStreamingTranscriber

        return AssemblyAIStreamingTranscriber
    from voci.stt import StreamingTranscriber

    return StreamingTranscriber


def main() -> int:
    args = parse_args()
    setup_logging(args.verbose)
    log = logging.getLogger("voci.main")

    cfg = AppConfig.load()
    if args.monitor_source:
        cfg.monitor_source = args.monitor_source

    if not cfg.monitor_source:
        log.error("No monitor source configured. Run scripts/list_audio_sources.py first.")
        return 2

    target_lang = args.target
    log.info(
        "voci starting: monitor=%s target=%s stt-backend=%s",
        cfg.monitor_source,
        target_lang,
        args.stt_backend,
    )

    if args.stt_backend == "deepgram" and not os.environ.get("DEEPGRAM_API_KEY"):
        log.error("--stt-backend deepgram requires DEEPGRAM_API_KEY in the environment.")
        return 4
    if args.stt_backend == "soniox" and not os.environ.get("SONIOX_API_KEY"):
        log.error("--stt-backend soniox requires SONIOX_API_KEY in the environment.")
        return 4
    if args.stt_backend == "assemblyai" and not os.environ.get("ASSEMBLYAI_API_KEY"):
        log.error("--stt-backend assemblyai requires ASSEMBLYAI_API_KEY in the environment.")
        return 4
    if args.translator == "cerebras" and not os.environ.get("CEREBRAS_API_KEY"):
        log.error("--translator cerebras requires CEREBRAS_API_KEY in the environment.")
        return 4

    StreamingTranscriber = _get_streaming_transcriber_class(args.stt_backend)

    capture = AudioCapture(
        monitor_source=cfg.monitor_source,
        sample_rate=cfg.sample_rate,
        block_seconds=cfg.block_seconds,
    )

    if args.headless:
        return _run_headless(
            cfg,
            capture,
            target_lang,
            no_translate=args.no_translate,
            transcriber_cls=StreamingTranscriber,
            translator_backend=args.translator,
        )
    return _run_gui(
        cfg,
        capture,
        target_lang,
        no_translate=args.no_translate,
        show_on_start=args.show_on_start,
        transcriber_cls=StreamingTranscriber,
        translator_backend=args.translator,
    )


def _run_headless(
    cfg: AppConfig,
    capture: AudioCapture,
    target_lang: str,
    no_translate: bool,
    transcriber_cls,
    translator_backend: str = "auto",
) -> int:
    log = logging.getLogger("voci.headless")
    t0 = time.monotonic()

    def stamp() -> str:
        return f"{time.monotonic() - t0:5.2f}s"

    if no_translate or target_lang == "en":

        def on_text(text: str, src: str) -> None:
            print(f"[{stamp()}] COMMIT en: {text}", flush=True)

        def on_partial(text: str, src: str) -> None:
            print(f"[{stamp()}]  partial en: {text}", flush=True)

        transcriber = transcriber_cls(
            audio_queue=capture.audio_queue,
            on_text=on_text,
            on_partial=on_partial,
            sample_rate=cfg.sample_rate,
        )
        tw = None
    else:
        translator = NllbTranslator(
            src_lang="en", target_lang=target_lang, backend=translator_backend
        )

        def on_translated(translated: str, src: str, tgt: str, is_partial: bool) -> None:
            tag = "ar~" if is_partial else "AR "
            print(f"[{stamp()}]   {tag}: {translated}", flush=True)

        tw = NllbTranslatorWorker(translator, on_translated=on_translated)

        def on_partial(text: str, src: str) -> None:
            print(f"[{stamp()}]  partial EN: {text}", flush=True)
            if text:
                tw.submit_partial(text, src)

        def on_text(text: str, src: str) -> None:
            print(f"[{stamp()}] COMMIT  EN: {text}", flush=True)
            tw.submit_final(text, src)

        transcriber = transcriber_cls(
            audio_queue=capture.audio_queue,
            on_text=on_text,
            on_partial=on_partial,
            sample_rate=cfg.sample_rate,
        )
        tw.start()

    capture.start()
    transcriber.start()

    stop = threading.Event()
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    log.info("Headless pipeline running. Ctrl-C to stop.")
    stop.wait()
    transcriber.stop()
    capture.stop()
    if tw is not None:
        tw.stop()
    return 0


def _run_gui(
    cfg: AppConfig,
    capture: AudioCapture,
    target_lang: str,
    no_translate: bool,
    show_on_start: bool,
    transcriber_cls,
    translator_backend: str = "auto",
) -> int:
    log = logging.getLogger("voci.gui")
    app = QApplication.instance() or QApplication(sys.argv)

    overlay = SubtitleOverlay(
        top_font=cfg.overlay_top_font,
        top_font_size=cfg.overlay_top_font_size,
        top_color_alpha=cfg.overlay_top_color_alpha,
        bottom_font=cfg.overlay_bottom_font,
        bottom_font_size=cfg.overlay_bottom_font_size,
        bottom_color_alpha=cfg.overlay_bottom_color_alpha,
        bottom_bold=cfg.overlay_bottom_bold,
        background_alpha=cfg.overlay_background_alpha,
        outline_blur=cfg.overlay_outline_blur,
        outline_alpha=cfg.overlay_outline_alpha,
        max_width_frac=cfg.overlay_max_width_frac,
        bottom_margin_px=cfg.overlay_bottom_margin_px,
    )
    if show_on_start:
        overlay.show()

    router = TextRouter()
    router.top_line.connect(overlay.set_top_text, Qt.QueuedConnection)
    router.bottom_line.connect(overlay.set_bottom_text, Qt.QueuedConnection)
    router.clear_lines.connect(overlay.clear_text, Qt.QueuedConnection)

    # Paginated mode: accumulate ALL spoken words; display only the words on
    # the current N-word page. Crossing a page boundary clears the line and
    # the next word starts a fresh page.
    state = {
        "last_activity": time.monotonic(),
        "en_finalized": "",  # committed English so far (accumulated)
        "en_provisional": "",  # latest stable partial extending finalized
        "ar_finalized": "",  # committed Arabic so far (accumulated)
        "ar_provisional": "",  # live translation of the current English partial
        "prev_partial_raw": "",
    }

    cleared_flag = {"v": False}

    def _mark_active() -> None:
        state["last_activity"] = time.monotonic()
        cleared_flag["v"] = False

    def _stable_prefix(a: str, b: str) -> str:
        """Longest common prefix between two strings, trimmed to a clean word boundary.
        Used to filter unstable trailing words from partial transcriptions so the overlay
        doesn't flicker (write/erase/rewrite cycle).
        """
        n = min(len(a), len(b))
        i = 0
        while i < n and a[i] == b[i]:
            i += 1
        prefix = a[:i]
        # Round back to last whitespace so we never display a half-word
        if i < len(a) and i < len(b):
            cut = prefix.rfind(" ")
            if cut > 0:
                prefix = prefix[:cut]
        return prefix.rstrip()

    def _slide_window(text: str, max_chars: int) -> str:
        """Keep only the last `max_chars` of `text`, snapped forward to a word boundary."""
        if len(text) <= max_chars:
            return text
        tail = text[-max_chars:]
        space = tail.find(" ")
        if space >= 0:
            tail = tail[space + 1 :]
        return tail

    def _paginate(text: str, words_per_page: int) -> str:
        """Show only the words on the CURRENT page. When word count crosses a page
        boundary (n*words_per_page), the previous page disappears and the new word
        starts a fresh page. So 10 words/page yields:
            words 1-10  -> shows all 10
            word 11     -> shows just word 11
            words 11-20 -> shows words 11-20
            word 21     -> shows just word 21
        """
        words = text.split()
        n = len(words)
        if n == 0:
            return ""
        page_start = (n - 1) // words_per_page * words_per_page
        return " ".join(words[page_start:])

    max_display_chars = int(cfg.overlay_max_display_chars)
    words_per_page = int(cfg.overlay_words_per_page)

    # State for LocalAgreement-2 partial stabilization
    state["prev_partial_raw"] = ""

    def _emit_english() -> None:
        full = state["en_finalized"]
        if state["en_provisional"]:
            full = (
                (full + " " + state["en_provisional"]).strip() if full else state["en_provisional"]
            )
        page = _paginate(full, words_per_page)
        router.top_line.emit(_slide_window(page, max_display_chars))

    def _emit_arabic() -> None:
        full = state["ar_finalized"]
        if state["ar_provisional"]:
            full = (
                (full + " " + state["ar_provisional"]).strip() if full else state["ar_provisional"]
            )
        page = _paginate(full, words_per_page)
        router.bottom_line.emit(_slide_window(page, max_display_chars))

    def _append(field: str, new_text: str) -> None:
        new_text = (new_text or "").strip()
        if not new_text:
            return
        state[field] = (state[field] + " " + new_text).strip() if state[field] else new_text

    stab_log = logging.getLogger("voci.stability")

    def _grow_provisional(stable: str) -> bool:
        """Strict-monotonic update of the displayed provisional within an
        utterance.

        Only accepts a new value if it CLEANLY EXTENDS the current displayed
        prefix (same prefix + new suffix). All other proposals — shorter,
        same length, or longer but rewrites an earlier word — are rejected
        and the display is held steady. The committed final on utterance end
        replaces everything anyway, so brief mid-utterance staleness is the
        right trade-off vs. word-level flicker the user can't read through.

        Returns True if the value actually changed.
        """
        prev = state["en_provisional"]
        if not stable or stable == prev:
            return False
        if len(stable) > len(prev) and stable.startswith(prev):
            state["en_provisional"] = stable
            stab_log.debug("extend: %r -> %r", prev, stable)
            return True
        # Disagreement: model rewrote earlier words or lost some. Hold.
        stab_log.debug("hold: kept %r, model proposed %r", prev, stable)
        return False

    if no_translate or target_lang == "en":

        def on_partial(text: str, src: str) -> None:
            _mark_active()
            stable = _stable_prefix(text, state["prev_partial_raw"])
            state["prev_partial_raw"] = text
            if _grow_provisional(stable):
                _emit_english()

        def on_text(text: str, src: str) -> None:
            _mark_active()
            _append("en_finalized", text)
            state["en_provisional"] = ""
            state["prev_partial_raw"] = ""
            stab_log.debug("commit final: %r", text)
            _emit_english()
            page = _paginate(state["en_finalized"], words_per_page)
            router.bottom_line.emit(_slide_window(page, max_display_chars))

        transcriber = transcriber_cls(
            audio_queue=capture.audio_queue,
            on_text=on_text,
            on_partial=on_partial,
            sample_rate=cfg.sample_rate,
        )
        tw = None
    else:
        translator = NllbTranslator(
            src_lang="en", target_lang=target_lang, backend=translator_backend
        )

        def on_translated(translated: str, src: str, tgt: str, is_partial: bool) -> None:
            if is_partial:
                state["ar_provisional"] = translated
            else:
                _append("ar_finalized", translated)
                state["ar_provisional"] = ""
            _emit_arabic()

        tw = NllbTranslatorWorker(translator, on_translated=on_translated)

        def on_partial(text: str, src: str) -> None:
            _mark_active()
            stable = _stable_prefix(text, state["prev_partial_raw"])
            state["prev_partial_raw"] = text
            # Only re-render and retranslate if our provisional actually grew.
            # When the model briefly "loses" a word, we hold the display
            # steady — and skip a wasted GPU translate call too.
            if _grow_provisional(stable):
                _emit_english()
                if state["en_provisional"]:
                    tw.submit_partial(state["en_provisional"], src)

        def on_text(text: str, src: str) -> None:
            _mark_active()
            _append("en_finalized", text)
            state["en_provisional"] = ""
            state["prev_partial_raw"] = ""
            stab_log.debug("commit final: %r", text)
            _emit_english()
            tw.submit_final(text, src)

        transcriber = transcriber_cls(
            audio_queue=capture.audio_queue,
            on_text=on_text,
            on_partial=on_partial,
            sample_rate=cfg.sample_rate,
        )
        tw.start()

    capture.start()
    transcriber.start()

    # S2: auto-clear after silence
    auto_clear_seconds = float(cfg.auto_clear_silence_seconds)

    def _check_silence() -> None:
        if auto_clear_seconds <= 0 or cleared_flag["v"]:
            return
        idle = time.monotonic() - state["last_activity"]
        if idle >= auto_clear_seconds:
            state["en_finalized"] = ""
            state["en_provisional"] = ""
            state["ar_finalized"] = ""
            state["ar_provisional"] = ""
            state["prev_partial_raw"] = ""
            router.clear_lines.emit()
            cleared_flag["v"] = True
            log.debug("auto-cleared overlay after %.1fs idle", idle)

    silence_timer = QTimer()
    silence_timer.timeout.connect(_check_silence)
    silence_timer.start(500)

    # Hotkeys
    hotkeys = HotkeyListener(
        hotkey_toggle=cfg.hotkey_toggle,
        hotkey_swap=cfg.hotkey_swap_lang,
        hotkey_drag=cfg.hotkey_drag,
        hotkey_clear=cfg.hotkey_clear,
    )

    def on_toggle_drag() -> None:
        # Read current click-through state and flip it
        currently_clickthrough = overlay.testAttribute(Qt.WA_TransparentForMouseEvents)
        new_state = not currently_clickthrough
        overlay.set_clickthrough(new_state)
        if new_state:
            log.info("Drag mode OFF (overlay is click-through again)")
        else:
            log.info("Drag mode ON — drag the overlay with the mouse")

    def on_clear() -> None:
        state["en_finalized"] = ""
        state["en_provisional"] = ""
        state["ar_finalized"] = ""
        state["prev_partial_raw"] = ""
        router.clear_lines.emit()
        cleared_flag["v"] = True
        log.info("Overlay cleared by hotkey")

    hotkeys.toggle_visibility.connect(overlay.toggle_visibility, Qt.QueuedConnection)
    hotkeys.toggle_drag_mode.connect(on_toggle_drag, Qt.QueuedConnection)
    hotkeys.clear_overlay.connect(on_clear, Qt.QueuedConnection)
    hotkeys.start()

    rc = app.exec()
    transcriber.stop()
    capture.stop()
    if tw is not None:
        tw.stop()
    hotkeys.stop()
    return rc


if __name__ == "__main__":
    sys.exit(main())

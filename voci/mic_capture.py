from __future__ import annotations

import logging
import shutil
import subprocess
import threading

log = logging.getLogger(__name__)


from typing import Callable


class MicStreamer:
    """Stream mic PCM frames (s16le, 16 kHz mono) from default PulseAudio source via `parec`.
    Each frame is delivered through `on_frame` callback as they arrive — used by dictate
    streaming mode to push directly into Deepgram's WebSocket.
    """

    SAMPLE_RATE = 16000
    FRAME_BYTES = 800  # 25 ms at 16 kHz mono s16le

    def __init__(self, on_frame: Callable[[bytes], None]) -> None:
        if shutil.which("parec") is None:
            raise RuntimeError("parec not found in PATH (install pulseaudio-utils)")
        self.on_frame = on_frame
        self._proc: subprocess.Popen[bytes] | None = None
        self._reader: threading.Thread | None = None
        self._stop = threading.Event()
        self._lock = threading.Lock()

    def start(self) -> None:
        with self._lock:
            if self._proc is not None:
                return
            self._stop.clear()
            cmd = [
                "parec",
                f"--rate={self.SAMPLE_RATE}",
                "--channels=1",
                "--format=s16le",
                "--latency-msec=15",
                "--client-name=voci-dictate",
            ]
            self._proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            proc = self._proc

            def _reader_loop() -> None:
                assert proc.stdout is not None
                buf = bytearray()
                while not self._stop.is_set():
                    data = proc.stdout.read(2048)
                    if not data:
                        return
                    buf.extend(data)
                    while len(buf) >= self.FRAME_BYTES:
                        try:
                            self.on_frame(bytes(buf[: self.FRAME_BYTES]))
                        except Exception as e:
                            log.error("on_frame failed: %s", e)
                        del buf[: self.FRAME_BYTES]

            self._reader = threading.Thread(target=_reader_loop, daemon=True, name="mic-streamer")
            self._reader.start()

    def stop(self) -> None:
        self._stop.set()
        with self._lock:
            if self._proc is not None:
                try:
                    self._proc.kill()  # SIGKILL — no graceful wait, want it gone fast
                except Exception:
                    pass
                self._proc = None
        if self._reader is not None:
            self._reader.join(timeout=0.3)
            self._reader = None


class MicRecorder:
    """Buffered mic capture (legacy REST-mode dictate). Captures into memory,
    returns full PCM bytes on stop()."""

    SAMPLE_RATE = 16000
    BYTES_PER_SAMPLE = 2  # s16le

    def __init__(self) -> None:
        if shutil.which("parec") is None:
            raise RuntimeError("parec not found in PATH (install pulseaudio-utils)")
        self._proc: subprocess.Popen[bytes] | None = None
        self._buf = bytearray()
        self._reader: threading.Thread | None = None
        self._lock = threading.Lock()

    def start(self) -> None:
        with self._lock:
            if self._proc is not None:
                return
            self._buf = bytearray()
            cmd = [
                "parec",
                f"--rate={self.SAMPLE_RATE}",
                "--channels=1",
                "--format=s16le",
                "--latency-msec=20",
                "--client-name=voci-dictate",
            ]
            log.debug("Starting mic: %s", " ".join(cmd))
            self._proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

            def _reader_loop(proc: subprocess.Popen[bytes]) -> None:
                assert proc.stdout is not None
                while True:
                    data = proc.stdout.read(4096)
                    if not data:
                        return
                    self._buf.extend(data)

            self._reader = threading.Thread(target=_reader_loop, args=(self._proc,), daemon=True, name="mic-reader")
            self._reader.start()

    def stop(self) -> bytes:
        with self._lock:
            if self._proc is None:
                return b""
            try:
                self._proc.terminate()
                self._proc.wait(timeout=1.5)
            except Exception:
                self._proc.kill()
            self._proc = None
        if self._reader is not None:
            self._reader.join(timeout=1.0)
            self._reader = None
        return bytes(self._buf)

from __future__ import annotations

import logging
import queue
import shutil
import subprocess
import threading

import numpy as np

log = logging.getLogger(__name__)


class AudioCapture:
    """Streams float32 mono blocks from a PulseAudio/PipeWire monitor source via `parec`.

    `parec` is part of pulseaudio-utils and works natively with PipeWire's
    PulseAudio compatibility layer. This avoids the PortAudio/ALSA dance —
    monitor sources are exposed by name directly.
    """

    BYTES_PER_SAMPLE = 4  # float32

    def __init__(
        self,
        monitor_source: str,
        sample_rate: int = 16000,
        block_seconds: float = 1.0,
        max_queue_seconds: float = 30.0,
    ) -> None:
        if shutil.which("parec") is None:
            raise RuntimeError("parec not found in PATH (install pulseaudio-utils)")
        self.monitor_source = monitor_source
        self.sample_rate = sample_rate
        self.block_samples = int(sample_rate * block_seconds)
        self.block_bytes = self.block_samples * self.BYTES_PER_SAMPLE
        max_blocks = max(1, int(max_queue_seconds / block_seconds))
        self.audio_queue: queue.Queue[np.ndarray] = queue.Queue(maxsize=max_blocks)
        self._proc: subprocess.Popen[bytes] | None = None
        self._reader: threading.Thread | None = None
        self._supervisor: threading.Thread | None = None
        self._stop = threading.Event()

    def _spawn(self) -> subprocess.Popen[bytes]:
        cmd = [
            "parec",
            f"--device={self.monitor_source}",
            f"--rate={self.sample_rate}",
            "--channels=1",
            "--format=float32le",
            "--latency-msec=30",
            "--client-name=voci",
        ]
        log.info("Spawning: %s", " ".join(cmd))
        return subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    def _read_loop(self, proc: subprocess.Popen[bytes]) -> None:
        assert proc.stdout is not None
        buf = bytearray()
        while not self._stop.is_set():
            need = self.block_bytes - len(buf)
            data = proc.stdout.read(need)
            if not data:
                log.warning("parec stdout closed")
                return
            buf.extend(data)
            if len(buf) < self.block_bytes:
                continue
            chunk = np.frombuffer(bytes(buf), dtype=np.float32).copy()
            buf.clear()
            try:
                self.audio_queue.put_nowait(chunk)
            except queue.Full:
                try:
                    self.audio_queue.get_nowait()
                except queue.Empty:
                    pass
                self.audio_queue.put_nowait(chunk)

    def _supervise(self) -> None:
        backoff = 1.0
        while not self._stop.is_set():
            proc: subprocess.Popen[bytes] | None = None
            try:
                proc = self._spawn()
                self._proc = proc
                reader = threading.Thread(
                    target=self._read_loop, args=(proc,), name="audio-reader", daemon=True
                )
                reader.start()
                self._reader = reader
                backoff = 1.0
                while not self._stop.is_set() and proc.poll() is None and reader.is_alive():
                    self._stop.wait(0.5)
                if proc.poll() is not None and not self._stop.is_set():
                    err = proc.stderr.read().decode(errors="replace") if proc.stderr else ""
                    log.error("parec exited (rc=%s): %s", proc.returncode, err.strip()[-300:])
            except Exception as e:
                log.error("Audio capture error: %s — reconnect in %.1fs", e, backoff)
            finally:
                if proc is not None and proc.poll() is None:
                    proc.terminate()
                    try:
                        proc.wait(timeout=1.5)
                    except subprocess.TimeoutExpired:
                        proc.kill()
            if self._stop.is_set():
                break
            self._stop.wait(backoff)
            backoff = min(backoff * 2, 15.0)

    def start(self) -> None:
        self._stop.clear()
        self._supervisor = threading.Thread(
            target=self._supervise, name="audio-supervisor", daemon=True
        )
        self._supervisor.start()

    def stop(self) -> None:
        self._stop.set()
        if self._proc is not None and self._proc.poll() is None:
            try:
                self._proc.terminate()
                self._proc.wait(timeout=1.5)
            except Exception:
                self._proc.kill()
        if self._supervisor is not None:
            self._supervisor.join(timeout=2.0)

from __future__ import annotations

import logging
import os
import time

import requests

log = logging.getLogger(__name__)


class DeepgramRest:
    """One-shot Deepgram transcription via the prerecorded REST endpoint.

    Used by dictate mode — sends the buffered mic audio after the user releases
    the hotkey. Faster than spinning up a streaming socket for short clips.
    """

    URL = "https://api.deepgram.com/v1/listen"

    def __init__(
        self,
        api_key: str | None = None,
        sample_rate: int = 16000,
        model: str = "nova-2",
        language: str = "en",
        smart_format: bool = True,
        punctuate: bool = True,
        keywords: list[str] | None = None,
    ) -> None:
        self.api_key = api_key or os.environ.get("DEEPGRAM_API_KEY")
        if not self.api_key:
            raise RuntimeError("DEEPGRAM_API_KEY not set")
        self.sample_rate = sample_rate
        self.model = model
        self.language = language
        self.smart_format = smart_format
        self.punctuate = punctuate
        self.keywords = keywords or []
        self._session = requests.Session()

    def transcribe(self, pcm_s16le: bytes) -> str:
        if not pcm_s16le:
            return ""
        params = {
            "model": self.model,
            "language": self.language,
            "encoding": "linear16",
            "sample_rate": str(self.sample_rate),
            "channels": "1",
            "smart_format": "true" if self.smart_format else "false",
            "punctuate": "true" if self.punctuate else "false",
        }
        if self.keywords:
            # Deepgram expects each keyword as a repeated `keywords` query param
            params_list: list[tuple[str, str]] = list(params.items())
            for kw in self.keywords:
                params_list.append(("keywords", kw))
            params = params_list  # type: ignore[assignment]
        headers = {
            "Authorization": f"Token {self.api_key}",
            "Content-Type": "audio/x-raw; encoding=linear16; sample-rate=16000; channels=1",
        }
        t0 = time.monotonic()
        r = self._session.post(self.URL, params=params, headers=headers, data=pcm_s16le, timeout=15)
        r.raise_for_status()
        data = r.json()
        try:
            transcript = data["results"]["channels"][0]["alternatives"][0]["transcript"]
        except (KeyError, IndexError):
            transcript = ""
        log.debug("Deepgram REST %.0fms -> %r", (time.monotonic() - t0) * 1000, transcript[:60])
        return (transcript or "").strip()

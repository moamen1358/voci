from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

CONFIG_PATH = Path.home() / ".config" / "voci" / "config.json"


@dataclass
class AppConfig:
    monitor_source: str | None = None
    sample_rate: int = 16000
    block_seconds: float = 0.025
    whisper_model: str = "large-v3-turbo"
    whisper_compute_type: str = "float16"
    whisper_device: str = "cuda"
    target_lang: str = "en"
    pivot_lang: str = "en"
    # Top line (live source-language partial)
    overlay_top_font: str = "Noto Sans"
    overlay_top_font_size: int = 16
    overlay_top_color_alpha: int = 180  # 0-255; lower = more "draft" looking
    # Bottom line (committed translation) — matches top styling per request
    overlay_bottom_font: str = "Noto Sans"
    overlay_bottom_font_size: int = 16
    overlay_bottom_color_alpha: int = 255
    overlay_bottom_bold: bool = False
    # Background/box
    overlay_background_alpha: int = 255  # fully opaque black
    overlay_outline_blur: int = 12  # heavier = stronger "outlined text" effect
    overlay_outline_alpha: int = 240
    overlay_max_width_frac: float = 0.72
    overlay_bottom_margin_px: int = 80
    # Paginated mode: every N words shown, then clear and start a new page
    overlay_words_per_page: int = 10
    overlay_max_display_chars: int = 200  # safety cap (per page)
    # Auto-clear after this many seconds of silence (0 = never; sentences only
    # disappear when replaced by a new one or via the clear hotkey)
    auto_clear_silence_seconds: float = 0.0
    hotkey_toggle: str = "<alt>+<ctrl>+v"
    hotkey_swap_lang: str = "<alt>+<ctrl>+l"
    hotkey_drag: str = "<alt>+<ctrl>+d"
    hotkey_clear: str = "<alt>+<ctrl>+x"

    def save(self, path: Path = CONFIG_PATH) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.__dict__, indent=2))

    @classmethod
    def load(cls, path: Path = CONFIG_PATH) -> AppConfig:
        if not path.exists():
            return cls()
        data = json.loads(path.read_text())
        return cls(**data)

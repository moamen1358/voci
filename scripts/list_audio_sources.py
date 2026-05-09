#!/usr/bin/env python3
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from voci.config import AppConfig, CONFIG_PATH


def default_sink_monitor() -> str | None:
    info = subprocess.run(["pactl", "info"], capture_output=True, text=True, check=True).stdout
    for line in info.splitlines():
        if line.startswith("Default Sink:"):
            sink = line.split(":", 1)[1].strip()
            return f"{sink}.monitor"
    return None


def list_sources() -> list[tuple[int, str, str]]:
    out = subprocess.run(
        ["pactl", "list", "sources", "short"], capture_output=True, text=True, check=True
    ).stdout
    rows: list[tuple[int, str, str]] = []
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) >= 4:
            rows.append((int(parts[0]), parts[1], parts[3]))
    return rows


def main() -> int:
    monitor = default_sink_monitor()
    print(f"Default sink monitor source: {monitor}\n")
    print("All sources:")
    monitors = []
    for idx, name, state in list_sources():
        marker = " <-- default monitor" if name == monitor else ""
        print(f"  [{idx}] {name}  ({state}){marker}")
        if name.endswith(".monitor"):
            monitors.append(name)

    if not monitor:
        print("\nERROR: could not detect default sink. Try `pactl info` manually.", file=sys.stderr)
        return 1

    cfg = AppConfig.load()
    cfg.monitor_source = monitor
    cfg.save()
    print(f"\nSaved monitor_source -> {CONFIG_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

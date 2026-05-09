# voci

voci is a real-time speech-to-text and translation overlay for Linux.
It captures system audio, transcribes it on the local GPU with NVIDIA
Parakeet, and renders the transcript in a transparent always-on-top
window. An optional second line shows the translation in any supported
target language.

The default pipeline runs entirely on the user's machine. First-token
latency is roughly **50 ms** — fast enough to keep up with live speech —
and no audio ever leaves the device. Cloud backends for both speech
recognition (Deepgram, AssemblyAI, Soniox) and translation (Cerebras,
Google Gemini) are available as opt-in alternatives when the local path
is not desired.

## What it looks like

```
┌──────────────────────────────────────────────────────────────────┐
│  this is the live english transcript appearing as you speak      │
│  هذه هي الترجمة العربية على السطر السفلي                            │
└──────────────────────────────────────────────────────────────────┘
```

A transparent window pinned above all other windows. Top line is the
source language, bottom line is the translation. The window is
draggable, can be cleared with a hotkey, and can be hidden without
quitting.

## Requirements

- Linux with PulseAudio or PipeWire (X11 recommended for global hotkeys)
- NVIDIA GPU with CUDA 12.x runtime for the local inference path
- Python 3.12, managed via [uv](https://github.com/astral-sh/uv)
- `pulseaudio-utils`, and one of `xdotool`, `wtype`, or `ydotool`

## Installation

```bash
git clone https://github.com/moamen1358/voci.git
cd voci
uv sync
```

The first `uv sync` pulls PyTorch and the speech-model dependencies and
takes several minutes. The Parakeet and OPUS-MT model weights
(~1 GB total) download on the **first launch** and are cached in
`~/.cache/huggingface/`. Subsequent launches are near-instant.

## Usage

List the available audio sources once and pick the one you want to
caption (typically your output device's monitor):

```bash
uv run python scripts/list_audio_sources.py
```

Then launch the overlay, passing your chosen source via
`--monitor-source` (or set it once in `voci/config.py`):

```bash
./run.sh --monitor-source alsa_output.pci-0000_00_1f.3.analog-stereo.monitor --target ar
```

Replace `ar` with any supported two-letter language code. Use
`--no-translate` for an English-only overlay.

For hold-to-talk dictation that types into the focused window:

```bash
./run.sh dictate
```

Run `./run.sh --help` for the full set of flags.

## Backends

### Speech-to-text

`--stt-backend` selects the speech engine.

| Value | Runs on | Requires |
|---|---|---|
| `parakeet` (default) | local CUDA GPU | — |
| `deepgram` | cloud WebSocket | `DEEPGRAM_API_KEY` |
| `assemblyai` | cloud WebSocket | `ASSEMBLYAI_API_KEY` |
| `soniox` | cloud WebSocket | `SONIOX_API_KEY` |

API keys live in `~/.config/voci/secrets.env` and are sourced
automatically by `run.sh`.

### Translation

`--translator` selects the translation engine.

| Value | Runs on | Notes |
|---|---|---|
| `auto` (default) | local CUDA GPU | OPUS-MT for supported pairs, NLLB-200 for the rest |
| `opus` | local CUDA GPU | Force OPUS-MT |
| `nllb` | local CUDA GPU | Force NLLB-200 |
| `cerebras` | cloud LLM | Requires `CEREBRAS_API_KEY` (1 M tokens/day free tier) |
| `gemini` | cloud LLM | Requires `GEMINI_API_KEY` (free tier rate-limited) |

When `cerebras` or `gemini` is selected, voci uses local OPUS-MT for
live partials and the chosen cloud model only for committed sentences,
so cloud rate limits do not affect responsiveness. Override the partial
backend explicitly with `--partial-translator
{auto,same,opus,nllb,cerebras,gemini}`. Override the cloud model with
`--cerebras-model <id>` or `--gemini-model <id>`.

### Display mode

`--translate-mode` controls how translated text is shown.

- `stable` (default) — only commit a translated line when the source
  text is finalized. Less flicker, slightly higher latency.
- `live` — translate every partial as it grows. Lower perceived
  latency, more visible text rewrites.

## Process control

```bash
./run.sh status   # list running voci processes and GPU memory usage
./run.sh kill     # terminate any running voci, including suspended ones
```

Stop the application with Ctrl-C. Ctrl-Z suspends the process and keeps
it holding GPU memory; `./run.sh kill` recovers from this state.

## Hotkeys

| Combination | Action |
|---|---|
| `Alt + Ctrl + V` | Toggle overlay visibility |
| `Alt + Ctrl + D` | Toggle drag-to-move |
| `Alt + Ctrl + X` | Clear current text |
| `Alt + Ctrl + L` | Swap source and target languages |

## Documentation

Architecture notes, per-backend implementation details, and contributor
guidance are in [CLAUDE.md](CLAUDE.md). All command-line flags are
listed by `./run.sh --help`.

## License

MIT.

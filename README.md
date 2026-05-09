# voci

voci is a real-time speech-to-text overlay for Linux. It captures system
audio with PulseAudio, transcribes it with NVIDIA Parakeet on the local
GPU, and displays the transcript in a transparent always-on-top window.
Optional translation via OPUS-MT, Cerebras, or Google Gemini renders a
second line in the target language.

The default pipeline runs entirely on the user's machine and requires no
API keys. Cloud backends for both speech recognition (Deepgram,
AssemblyAI, Soniox) and translation (Cerebras, Gemini) are available as
opt-in alternatives.

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
takes several minutes. Subsequent invocations are near-instant.

## Usage

Select an audio source once before the first run:

```bash
uv run python scripts/list_audio_sources.py
```

Then launch the overlay:

```bash
./run.sh --target ar
```

The overlay shows the English transcript on the top line and the
translated text on the bottom line. Replace `ar` with any supported
two-letter language code. Use `--no-translate` for an English-only
overlay.

For hold-to-talk dictation that types into the focused window:

```bash
./run.sh dictate
```

Run `./run.sh --help` for the full set of flags.

## Backends

`--stt-backend` selects the speech-to-text engine. The default
`parakeet` runs locally on CUDA. The alternatives `deepgram`,
`assemblyai`, and `soniox` are cloud WebSocket services and require
their corresponding API keys in `~/.config/voci/secrets.env`.

`--translator` selects the translation engine. The default `auto` uses
OPUS-MT for supported language pairs and falls back to NLLB-200.
`cerebras` and `gemini` route translation through cloud LLMs.

When `cerebras` or `gemini` is selected, voci uses local OPUS-MT for
live partials and the chosen cloud model only for committed sentences,
so cloud rate limits do not affect responsiveness.

## Process control

```bash
./run.sh status   # list running voci processes and GPU memory usage
./run.sh kill     # terminate any running voci, including suspended ones
```

Stop the application with Ctrl-C. Ctrl-Z suspends the process and
keeps it holding GPU memory; `./run.sh kill` recovers from this state.

## Hotkeys

| Combination | Action |
|---|---|
| `Alt + Ctrl + V` | Toggle overlay visibility |
| `Alt + Ctrl + D` | Toggle drag-to-move |
| `Alt + Ctrl + X` | Clear current text |
| `Alt + Ctrl + L` | Switch target language |

## Documentation

Architecture notes and per-backend implementation details are in
[CLAUDE.md](CLAUDE.md). All command-line flags are listed by
`./run.sh --help`.

## License

MIT.

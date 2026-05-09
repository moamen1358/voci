# voci

A real-time speech-to-text + translation overlay for Linux. Captures whatever
audio is playing on your machine (or your microphone), runs streaming speech
recognition, optionally translates the result into another language, and floats
the live subtitles in a transparent always-on-top window pinned over whatever
you're watching.

Built for one specific workflow: watching English-language video and reading
live Arabic subtitles on top of it without any cloud dependency required, on a
single laptop with an NVIDIA GPU. Everything else (cloud STT, cloud translation
LLMs, hotkey-driven dictation) is layered on as an opt-in.

---

## How it works

```
┌────────────────────────────────────────────────┐
│  System audio (PulseAudio / PipeWire monitor)  │  whatever's playing
└────────────────────┬───────────────────────────┘
                     │ float32 16 kHz mono frames
                     ▼
┌────────────────────────────────────────────────┐
│  AudioCapture       (voci/audio_capture.py)    │  parec subprocess,
│                                                │  auto-reconnect
└────────────────────┬───────────────────────────┘
                     │ queue of audio chunks
                     ▼
┌────────────────────────────────────────────────┐
│  StreamingTranscriber                          │  one of:
│                                                │   • Parakeet (local GPU)
│                                                │   • AssemblyAI (cloud)
│                                                │   • Deepgram   (cloud)
│                                                │   • Soniox     (cloud)
└──────┬─────────────────────────────────────────┘
       │ on_partial(text)   on_text(text)        the partial keeps growing,
       │ live partials      committed sentences  the final is immutable
       ▼
┌────────────────────────────────────────────────┐
│  Top overlay line                              │  English subtitle
│  (LineOverlay, frameless transparent QWidget)  │  with monotonic
│                                                │  growth — never
│                                                │  flickers
└────────────────────────────────────────────────┘
       │
       │ each committed English sentence
       ▼
┌────────────────────────────────────────────────┐
│  Translator        (voci/translate/_worker.py) │  one of:
│                                                │   • OPUS-MT  (local)
│                                                │   • NLLB-200 (local)
│                                                │   • Cerebras (cloud LLM)
│                                                │   • Gemini   (cloud LLM)
└──────┬─────────────────────────────────────────┘
       │ Arabic translation
       ▼
┌────────────────────────────────────────────────┐
│  Bottom overlay line                           │  Arabic subtitle
└────────────────────────────────────────────────┘
```

Two design choices keep the UX clean:

1. **Monotonic display.** Once a word appears on screen during a sentence, it
   stays there until that sentence commits — even if the speech model briefly
   changes its mind about an earlier word. This kills the
   "word-appears-then-disappears-then-reappears" flicker that plagues most
   real-time captioning systems.
2. **Stable translation.** Arabic only updates when an English sentence
   commits, not on every partial. Translating partial English produces wildly
   different Arabic each time (Arabic word order reverses English), so
   live-updating Arabic flickers in a way that's impossible to read. Default
   behavior is "Arabic appears in clean chunks shortly after each English
   sentence ends" — like normal TV subtitles.

---

## Quick start

```bash
# 1. Pick which audio source to capture (writes to ~/.config/voci/config.json)
uv run python scripts/list_audio_sources.py

# 2. Subtitle only — English subtitles, no translation
./run.sh --no-translate

# 3. Subtitle + Arabic translation (default)
./run.sh --target ar
```

First launch downloads the model weights (Parakeet ~600 MB, OPUS-MT en-ar
~300 MB) into `~/.cache/huggingface/` and `~/.cache/voci/`. Subsequent
launches load from cache in about 15 seconds.

---

## Modes

### Subtitle only (English on screen, no translation)

```bash
./run.sh --no-translate
```

Both overlay lines render English — the top line shows the live partial as
you speak, the bottom line shows committed sentences as they arrive.

### Subtitle + translation (English top, Arabic bottom)

```bash
./run.sh --target ar
```

`--target` accepts any 2-letter ISO code. Default is `ar`. Languages with
dedicated OPUS-MT models are fastest (`ar`, `es`, `fr`, `de`, `it`, `pt`,
`ru`, `ja`, `zh`, `ko`, `tr`, `nl`, `fa`); others fall back to NLLB-200.

### Dictation (hold-to-talk → typed into the focused window)

```bash
./run.sh dictate                                     # F9 to record
./run.sh dictate --target ar                         # translate before typing
./run.sh dictate --hotkey '<ctrl>+<alt>+space'
```

Hold the hotkey while talking, release when done. The transcribed text is
pasted into whatever window has focus.

---

## Speech recognition backends

Picked with `--stt-backend <name>`. Default is `parakeet` (local).

| Backend | Where it runs | What it's good for |
|---|---|---|
| `parakeet` (default) | Local NVIDIA GPU via NeMo | No network, no API key, no per-use cost. Pseudo-streaming via rolling buffer. |
| `assemblyai` | Cloud WebSocket | Immutable finals — once a word lands, the model promises it won't be revised. Best stability. |
| `deepgram` | Cloud WebSocket | Battle-tested low-latency cloud STT with the longest history of streaming support. |
| `soniox` | Cloud WebSocket | Lowest cloud latency available, sub-200 ms first token. |

The local Parakeet path uses NVIDIA's [Parakeet TDT 0.6b v2](https://huggingface.co/nvidia/parakeet-tdt-0.6b-v2)
model — currently top of HuggingFace's English ASR leaderboard. Cloud
backends are wired in case you want immutable finals (AssemblyAI), the
absolute lowest first-token latency (Soniox), or you're running on a machine
without a GPU.

---

## Translation backends

Picked with `--translator <name>`. Default `auto` selects OPUS-MT for
supported pairs and NLLB-200 otherwise.

| Backend | Where it runs | What it's good for |
|---|---|---|
| `auto` (default) | Local GPU | OPUS-MT for known pairs, NLLB-200 fallback. |
| `opus` | Local GPU | [Helsinki-NLP OPUS-MT](https://huggingface.co/Helsinki-NLP) — bilingual ~80 M-parameter MarianMT models, ~15 ms inference. |
| `nllb` | Local GPU | [Meta NLLB-200 distilled-600M](https://huggingface.co/facebook/nllb-200-distilled-600M) — universal coverage of 200 languages. |
| `cerebras` | Cloud LLM | Calls a chat model on Cerebras Inference (default `gpt-oss-120b`). Pick the model with `--cerebras-model`. |
| `gemini` | Cloud LLM | Google Gemini via AI Studio (default `gemini-2.5-flash`). Pick the model with `--gemini-model`. |

LLM-based translation (cerebras, gemini) tends to produce more
context-aware Arabic than the bilingual MarianMT models, especially on
sentences with idioms or context-dependent meaning.

### Hybrid translation (automatic for cloud LLMs)

When you pick `cerebras` or `gemini` as the translator, voci automatically
splits the work:

- **Live partials** (the running English transcript as you speak) get
  translated by local OPUS-MT — fast, no rate limits.
- **Committed sentences** (after each end-of-utterance) get re-translated
  by the cloud LLM you chose.

This keeps the live Arabic responsive while reserving the cloud-LLM call for
the one final translation per sentence. Override with `--partial-translator
{auto,same,opus,nllb,cerebras,gemini}`.

### Translate mode

```bash
--translate-mode stable    # default — translate only on sentence commits, never flickers
--translate-mode live      # also translate partials — Arabic updates word-by-word but flickers
```

Stable is the default because Arabic word order reverses English, so each
new partial English word makes the translator rewrite the whole Arabic
sentence. Live mode is available if you prefer responsive-but-flickery
Arabic over delayed-but-stable.

---

## Process control

```bash
./run.sh status     # show running voci processes + GPU memory usage
./run.sh kill       # cleanly terminate (handles processes you Ctrl-Z'd)
```

Stop the app with **Ctrl-C**, not Ctrl-Z. Ctrl-Z suspends the process but
keeps it holding GPU memory, which causes CUDA out-of-memory errors next
time you launch. If you forget, `./run.sh kill` cleans up.

---

## Hotkeys (overlay mode)

| Combo | Action |
|---|---|
| `Alt+Ctrl+V` | Toggle overlay visibility |
| `Alt+Ctrl+D` | Toggle drag-to-move (vs click-through) |
| `Alt+Ctrl+X` | Clear current text |
| `Alt+Ctrl+L` | Swap target language |

---

## API keys

If you use a cloud backend, put the keys in `~/.config/voci/secrets.env`:

```
DEEPGRAM_API_KEY=...
ASSEMBLYAI_API_KEY=...
SONIOX_API_KEY=...
CEREBRAS_API_KEY=...
GEMINI_API_KEY=...
```

`run.sh` sources this file automatically. The app fails fast with a clear
error message if you select a backend whose key isn't set.

---

## What's in the repo

```
voci/
  main.py                  # overlay mode entry point
  dictate.py               # hold-to-talk dictation
  audio_capture.py         # PulseAudio monitor source via parec
  mic_capture.py           # mic source for dictation
  overlay.py               # transparent always-on-top Qt windows
  hotkey.py                # global hotkey listener (pynput)
  typer.py                 # clipboard / keyboard injection
  dictate_indicator.py     # animated recording indicator
  config.py                # AppConfig dataclass + JSON load/save
  __init__.py              # CUDA library preload for ctranslate2

  stt/                     # speech-recognition backends, one file each
    _model.py              # process-wide singleton Parakeet loader
    parakeet_streaming.py  # local pseudo-streaming for the overlay
    parakeet_dictate.py    # local hold-to-talk for dictation
    deepgram.py            # cloud Deepgram WebSocket
    soniox_stt.py          # cloud Soniox WebSocket
    assemblyai_stt.py      # cloud AssemblyAI Universal-Streaming v3

  translate/               # translation backends
    _worker.py             # off-thread worker; partials drop, finals queue
    opus_mt.py             # local Helsinki-NLP OPUS-MT (CTranslate2)
    nllb.py                # local Meta NLLB-200 (CTranslate2)
    cerebras_llm.py        # cloud Cerebras LLMs
    gemini_llm.py          # cloud Google Gemini

scripts/
  list_audio_sources.py    # PulseAudio source picker → ~/.config/voci/config.json
  capture_probe.py         # smoke-test parec capture
  test_overlay.py          # visual test of the subtitle overlay
  bench_parakeet.py        # latency benchmark

run.sh                     # launcher (sources secrets.env, validates CUDA, dispatches)
pyproject.toml             # uv-managed deps + ruff/pyright config
.python-version            # 3.12 pin
```

---

## Requirements

- Linux (X11 recommended; Wayland works for the overlay but global hotkeys
  are flaky there)
- NVIDIA GPU with CUDA 12.x runtime (for the local Parakeet/OPUS-MT path)
- `uv` for environment management
- `parec` (from `pulseaudio-utils`) — works against both PulseAudio and
  PipeWire's compatibility layer
- `xdotool`, `wtype`, or `ydotool` for the dictation paste keystroke

Python 3.12 is pinned — NeMo isn't fully compatible with 3.13 yet.

---

## License

MIT.

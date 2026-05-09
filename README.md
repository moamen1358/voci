# voci

Real-time speech-to-text + translation overlay for Linux. Captures system audio
(or microphone), runs streaming STT, optionally translates to a target
language, and floats subtitles in a transparent always-on-top window.

Default stack runs **fully local on an NVIDIA GPU** (no API keys needed).
Optional cloud backends are wired in for STT and translation if you want
better latency or quality.

---

## Quick start

```bash
# 1. Pick which audio source to capture (writes monitor source to ~/.config/voci/config.json)
uv run python scripts/list_audio_sources.py

# 2. Subtitle only — English subtitles, no translation
./run.sh --no-translate

# 3. Subtitle + Arabic translation (default)
./run.sh --target ar
```

That's it for the local-only path. First launch downloads ~1.8 GB of model
weights (Parakeet ~600 MB, OPUS-MT en-ar ~300 MB, NLLB fallback if needed
~700 MB). Subsequent launches load from cache in ~15 s.

---

## Modes

### Subtitle only (English on the screen, nothing else)

```bash
./run.sh --no-translate                                  # local Parakeet
./run.sh --no-translate --stt-backend assemblyai         # cloud AssemblyAI
./run.sh --no-translate --stt-backend deepgram           # cloud Deepgram
```

Both overlay lines will show the English transcript (top = live partial as
you speak, bottom = committed sentences).

### Subtitle + translation (English top, Arabic bottom)

```bash
./run.sh --target ar                                                       # local STT + local translation
./run.sh --stt-backend assemblyai --target ar                              # cloud STT + local translation
./run.sh --stt-backend assemblyai --translator cerebras --target ar        # cloud STT + Cerebras LLM
./run.sh --stt-backend assemblyai --translator gemini --target ar          # cloud STT + Gemini
```

### Dictation (hold-to-talk → typed into focused window)

```bash
./run.sh dictate                          # F9 to record
./run.sh dictate --target ar              # translate to Arabic before typing
./run.sh dictate --hotkey '<ctrl>+<alt>+space'
```

---

## STT (speech recognition) backends

| Backend | Latency | Cost | Setup |
|---|---|---|---|
| `parakeet` (default) | ~250-400 ms | Free, local on GPU | Nothing — auto-downloads on first run |
| `assemblyai` | ~250 ms (immutable finals = no flicker) | $50 / ~333 hrs free, then ~$0.15/hr | `ASSEMBLYAI_API_KEY` |
| `deepgram` | ~300 ms | $200 / ~430 hrs free, then ~$0.46/hr | `DEEPGRAM_API_KEY` |
| `soniox` | sub-200 ms | tiny free credit, then $0.12/hr | `SONIOX_API_KEY` |

Pick with `--stt-backend <name>`.

**Recommendation**: `assemblyai` for cloud (immutable finals = clean, generous
free tier); `parakeet` for offline / no-network use.

---

## Translation backends

| Backend | Latency | Cost | Setup |
|---|---|---|---|
| `auto` (default) | ~15 ms | Free, local | Nothing |
| `opus` | ~15 ms | Free, local | Nothing — same as auto for en→ar |
| `nllb` | ~50-100 ms | Free, local | Nothing |
| `cerebras` | ~80-150 ms | 1 M tokens/day free | `CEREBRAS_API_KEY` |
| `gemini` | ~200-400 ms | 10 RPM free, paid ~$0.04/hr | `GEMINI_API_KEY` |

Pick with `--translator <name>`. Default `auto` picks **OPUS-MT** for known
pairs (en→ar/es/fr/de/it/pt/ru/ja/zh/tr/nl/fa) and falls back to **NLLB-200**
for any other target.

### Picking a model within a backend

```bash
./run.sh --translator cerebras --cerebras-model zai-glm-4.7 --target ar     # 355 B params, strongest Arabic on Cerebras
./run.sh --translator gemini --gemini-model gemini-2.5-pro --target ar      # highest Gemini quality
```

### Hybrid translation (fast partials + quality finals)

When you pick a rate-limited cloud translator (`cerebras` or `gemini`), voci
**automatically** uses local OPUS-MT for live partial translations and the
chosen cloud model only for committed sentences. This stays under free-tier
RPM limits and avoids "translation lag" while keeping high quality on each
final.

Override via `--partial-translator`:

```bash
--partial-translator opus     # always use local OPUS-MT for partials (default with cloud)
--partial-translator same     # use the same backend for both (will hit cloud RPM limits)
--partial-translator cerebras # mix: cerebras for partials + gemini for finals
```

### Translate mode (when to translate)

```bash
--translate-mode stable    # translate only on sentence commits — no flicker (default, recommended)
--translate-mode live      # also translate every partial — Arabic updates word-by-word but flickers because Arabic word order rewrites with each new English word
```

---

## API keys

If you use any cloud backend, put the keys in `~/.config/voci/secrets.env`:

```
DEEPGRAM_API_KEY=...      # https://console.deepgram.com/
ASSEMBLYAI_API_KEY=...    # https://www.assemblyai.com/
SONIOX_API_KEY=...        # https://console.soniox.com/
CEREBRAS_API_KEY=...      # https://cloud.cerebras.ai/
GEMINI_API_KEY=...        # https://aistudio.google.com/apikey
```

`run.sh` sources this file automatically. App fails fast with a clear message
if you select a backend whose key isn't set.

---

## Process control

```bash
./run.sh status     # show running voci processes + GPU memory usage
./run.sh kill       # cleanly terminate everything (handles Ctrl-Z'd processes too)
```

**Important**: stop the app with **Ctrl-C** (not Ctrl-Z). Ctrl-Z suspends the
process but keeps it holding GPU memory — you'll get CUDA OOM next time you
launch. If you forget, `./run.sh kill` cleans up.

---

## Hotkeys (overlay mode)

| Combo | Action |
|---|---|
| `Alt+Ctrl+V` | Toggle overlay visibility |
| `Alt+Ctrl+D` | Toggle drag-to-move (vs click-through) |
| `Alt+Ctrl+X` | Clear current text |
| `Alt+Ctrl+L` | Swap target language |

---

## What we built

The project went through eight major iterations. Each commit message has the
full context if you want detail:

1. **Removed Android implementation** — focused on Linux desktop only.
2. **Local STT + translation** — replaced cloud Deepgram + MyMemory with
   NVIDIA Parakeet (NeMo) and NLLB-200 (CTranslate2), both running on GPU.
3. **Modern Python tooling** — `uv` + `ruff` + `pyright`, `pyproject.toml`.
4. **Live partial translation** — Arabic tracks English in real time via
   OPUS-MT (~15 ms), with a worker that drops stale partials.
5. **GPU OOM survivability** — translators auto-fall-back from CUDA to CPU
   when out of memory; allocator tuned via `PYTORCH_CUDA_ALLOC_CONF`.
6. **Monotonic English overlay** — words never appear-then-disappear during
   a sentence; whatever's on screen stays put until commit.
7. **Process control** — `./run.sh status` and `./run.sh kill` handle
   orphaned/suspended processes cleanly.
8. **Cloud STT alternatives** — Deepgram, Soniox, AssemblyAI Universal-Streaming
   wired behind a `--stt-backend` flag. AssemblyAI's immutable-finals
   contract eliminates STT-side flicker entirely.
9. **Cloud translation alternatives** — Cerebras (Llama/Qwen/GPT-OSS/GLM) and
   Google Gemini wired behind `--translator`. Hybrid mode (OPUS-MT partials
   + cloud finals) automatically engaged for rate-limited backends.
10. **Stable translation default** — Arabic only updates on sentence commits
    (no mid-sentence reordering flicker); `--translate-mode live` opts back
    in to live partials.
11. **Aggressive AssemblyAI endpointing** — committed sentences detected ~3×
    faster (~160 ms vs default ~400 ms of silence).

---

## Known issues & future improvements

These are real gaps you'll hit if you push voci hard, ranked by how much they'd
improve daily use:

### High impact

- **Translation cache.** Same English phrase → translate once, reuse forever.
  Live captioning has a lot of repeated chunks ("I think...", "you know...",
  greetings). A simple LRU cache keyed on the English string would cut
  cloud-LLM call rate by 30-50% and eliminate flicker on repeated phrases.

- **Single-line subtitle mode.** Currently both overlay lines render even
  when only one has content (`--no-translate` shows English on both lines).
  A `--single-line` flag would render only one window — cleaner for
  English-only watching.

- **Speech-translation models** (one-step instead of STT → MT). Models like
  Meta's SeamlessM4T or NVIDIA Canary take English audio and emit Arabic
  text directly. Cuts the whole MT round-trip and tends to handle word
  order better than two-stage. Big architectural change but worth exploring
  if translation quality matters more than English subtitle quality.

- **Context-aware translation.** Send the previous 1-2 sentences as context
  to the cloud LLM. Improves Arabic quality on sentences that depend on
  prior context (pronouns, references). ~20 extra tokens per call, worth it.

### Medium impact

- **Configurable endpointing thresholds** via CLI flag for AssemblyAI.
  The aggressive defaults (160 ms silence) sometimes commit mid-sentence on
  natural breath pauses. Should be tunable without editing code.

- **Auto-retry / failover** on cloud rate limits. Today if Gemini 429s, the
  partial just disappears. Better: catch 429, fall back to OPUS-MT for that
  one call, log the rate-limit event.

- **Subtitle log to file.** Save every committed English + Arabic line with
  timestamps to `~/.cache/voci/sessions/<date>.jsonl`. Useful for review
  and for testing translation backends offline.

- **Cache-aware Parakeet streaming.** The current local Parakeet uses a
  rolling-buffer approach (~250-400 ms first-token) because Parakeet TDT
  0.6b v2 is trained with full attention. Switching to
  `nvidia/stt_en_fastconformer_hybrid_large_streaming_multi` would unlock
  true cache-aware streaming at ~50-100 ms first-token, free.

### Low impact (polish)

- **Wayland-native hotkeys.** Current `pynput` works best on X11. If you
  switch to Wayland the hotkeys become unreliable.

- **Overlay theming.** Font, color, opacity, position — currently config-
  file-only. A `voci config` CLI command would be faster.

- **Profiles.** Ship preset configurations: "watch-youtube", "study-arabic",
  "dictate-emails", each with its own backend stack and translate mode.

- **Multi-monitor positioning.** Overlay always lands on the primary
  monitor; should follow the focused window or accept `--monitor 2`.

- **Pause/resume hotkey.** Currently you have to kill and relaunch to stop
  capturing. A pause hotkey would let you suspend without losing the model
  warmup.

---

## Files

```
voci/
  main.py                  # overlay mode entry point
  dictate.py               # hold-to-talk dictation
  audio_capture.py         # PulseAudio monitor source via parec
  mic_capture.py           # mic source for dictation
  overlay.py               # transparent always-on-top Qt windows
  hotkey.py                # global hotkey listener (pynput)
  typer.py                 # clipboard/keyboard injection
  dictate_indicator.py     # animated recording indicator
  config.py                # AppConfig dataclass + JSON load/save
  __init__.py              # CUDA library preload for ctranslate2

  stt/                     # STT backends (one per file)
    __init__.py            # exports local Parakeet StreamingTranscriber + DictateSTT
    _model.py              # process-wide singleton Parakeet loader
    parakeet_streaming.py  # local rolling-buffer streaming
    parakeet_dictate.py    # local hold-to-talk
    deepgram.py            # cloud Deepgram WebSocket
    soniox_stt.py          # cloud Soniox WebSocket
    assemblyai_stt.py      # cloud AssemblyAI Universal-Streaming v3

  translate/               # translation backends
    __init__.py            # factory: picks backend by --translator + language pair
    _worker.py             # off-thread worker; partials drop, finals queue
    opus_mt.py             # local Helsinki-NLP OPUS-MT (CTranslate2)
    nllb.py                # local Meta NLLB-200 (CTranslate2)
    cerebras_llm.py        # cloud Cerebras LLMs
    gemini_llm.py          # cloud Google Gemini

scripts/
  list_audio_sources.py    # PulseAudio source picker → ~/.config/voci/config.json
  capture_probe.py         # smoke-test parec capture
  test_overlay.py          # visual test of subtitle overlay
  bench_parakeet.py        # latency benchmark

run.sh                     # launcher (sources secrets.env, validates CUDA, dispatches)
pyproject.toml             # uv-managed deps + ruff/pyright config
.python-version            # 3.12 pin
```

---

## License

MIT.

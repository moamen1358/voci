# voci — Claude session memory

Read this before doing anything in this repo. README.md has the user-facing
docs; this file has the context Claude needs to pick up where the last
session left off.

(Project was originally named STTL on disk and on GitHub. Renamed to
`voci` on 2026-05-09 — both the local folder (`~/Desktop/voci/`) and
the GitHub repo (`github.com/moamen1358/voci.git`). GitHub keeps the
old URL working as a redirect, but the canonical name is `voci`.)

## What this project is

Real-time speech-to-text + Arabic translation overlay for Linux. Captures
system audio via `parec` (PulseAudio/PipeWire), runs streaming STT, optionally
translates to a target language, displays subtitles in a transparent
always-on-top Qt overlay. Hardware target: RTX 4060 Laptop GPU (8 GB).

## What's been decided (don't re-litigate)

- **Language: Python 3.12.** Rust/C++ were considered and rejected — the
  bottleneck is CUDA inference, not host language. Documented in commit
  `1df1cd3`. Don't propose rewriting.
- **Default STT: NVIDIA Parakeet TDT 0.6b v2 (NeMo)**, runs locally on
  CUDA FP16. Discovered mid-project that this model is trained with full
  attention (no cache-aware streaming). Current implementation uses a
  rolling-buffer pseudo-streaming approach (~250-400 ms first-token).
  True streaming would require switching to
  `nvidia/stt_en_fastconformer_hybrid_large_streaming_multi`.
- **Default translator: OPUS-MT** (Helsinki-NLP, ~80M params per pair) via
  CTranslate2 FP16. Falls back to **NLLB-200 distilled-600M** for
  unsupported language pairs.
- **Cloud STT options wired**: `deepgram`, `soniox`, `assemblyai`. Soniox
  free tier is microscopic — burned in minutes during testing. AssemblyAI
  Universal-Streaming v3 is the recommended cloud STT (immutable finals
  fix flicker by design, $50 ≈ 333 hr free).
- **Cloud translator options wired**: `cerebras` (Llama/Qwen/GPT-OSS/GLM,
  1 M tokens/day free) and `gemini` (Google AI Studio).
- **Stable translation is default.** Live partial translation flickers
  badly because Arabic word order rewrites the whole sentence on each
  partial. `--translate-mode live` opts back in.
- **Hybrid translation auto-engaged for cloud LLMs.** `cerebras` and
  `gemini` get OPUS-MT for partials + chosen LLM for finals so they stay
  under free-tier RPM limits.

## What the user cares about (priorities, in order)

1. **Stable Arabic** — Arabic translation must not flicker. They tolerated
   live partials briefly then switched back to wanting stability. Don't
   default to live mode again.
2. **Free / cheap.** They explicitly preferred free credits over paid.
   They eventually accepted Gemini paid tier ($0.04/hr) for quality but
   then complained when it was too slow. The good free middle ground is
   Cerebras `zai-glm-4.7` (355 B params, strong Arabic).
3. **Low latency, but not at the cost of stability or quality.** They want
   Arabic to appear within a few hundred ms of an English commit, but they
   prefer slow + stable to fast + flickery.
4. **Simple, working stack.** They want one command that just works, not
   30 flags to tune.

## Current recommended command (as of session end 2026-05-09)

```bash
./run.sh --stt-backend assemblyai --translator cerebras \
         --cerebras-model zai-glm-4.7 --target ar
```

Best free combination of low-latency English STT + best Arabic quality on
Cerebras free tier. `--translate-mode stable` is the default so Arabic
won't flicker.

## Important user preferences learned this session

- **They use Ctrl-Z to "stop" the app** — that suspends instead of killing,
  the process keeps holding GPU memory, causes OOM next launch. Implemented
  `./run.sh kill` and `./run.sh status` to recover. Tell them to use
  Ctrl-C, not Ctrl-Z. They'll forget.
- **Their Obsidian vault is on `/media/moamen/inVisA1/_obsidian-vault/`.**
  They explicitly asked to remove all STTL/voci traces from the vault at
  end of this session — don't auto-log to it. The `log-mutation.sh` hook
  was patched to opt-out STTL and `Desktop/voci/`.
- **They prefer stuff on the external drive `/media/moamen/inVisA1/`.**
  Project lives at `~/Desktop/voci/` (renamed from `~/Desktop/STTL/` on
  2026-05-09); not worth moving to inVisA1 without asking.
- **They opened `~/.config/voci/secrets.env` in the IDE several times** to
  add API keys. Currently has DEEPGRAM_API_KEY, ASSEMBLYAI_API_KEY,
  CEREBRAS_API_KEY, GEMINI_API_KEY (last user check).
- **They tested Soniox once and hit 402 (paywalled).** Don't recommend it
  again unless they top up their account.
- **Translation model preferences observed**:
  - Llama 3.1 8B → bad Arabic (rejected)
  - gpt-oss-120b → not yet tested by user as of session end
  - zai-glm-4.7 → recommended but not yet tested
  - Gemini 2.5 Flash → quality acceptable but rate-limited on free, slow on free tier

## Repo state

- Branch: `main`
- Pushed to GitHub: `https://github.com/moamen1358/voci.git` (was `STTL.git` until 2026-05-09)
- Last commits (newest first):
  - `b214a9b` Add README
  - `ad9b7b7` Aggressive endpointing on AssemblyAI to cut commit lag
  - `3c56aa9` Add --translate-mode {stable,live}; default to stable
  - `87750fc` Hybrid translation: fast partials + quality finals
  - `dc594fb` Add Google Gemini translator backend
  - `d4c3e79` Switch Cerebras default to gpt-oss-120b
  - `5bab59e` Coalesce audio into 100 ms chunks for AssemblyAI
  - `fd82cb3` Add AssemblyAI Universal-Streaming v3 backend
  - `3798b59` Fix Soniox max_endpoint_delay_ms minimum (500 ms)
  - `02c0707` Add Soniox STT + Cerebras LLM translator
  - `140472e` Add Deepgram backend for A/B comparison
  - `d7a5dba` Add 'status' and 'kill' subcommands
  - `7764467` Stop overlay flicker: monotonic provisional
  - `d024c74` Translators GPU-OOM-survivable
  - `24cb181` Translate live English partials
  - `e642139` Adopt uv + ruff + pyright
  - `1df1cd3` Migrate to fully local Parakeet + NLLB
  - `b62e113` Remove Android implementation
  - `c051791` Initial commit

## Open improvements (from README, prioritized)

Not yet implemented; pick from these when user says "make it better":

1. **Translation cache** — LRU on English string → cached Arabic. Probably
   halves cloud-LLM call rate, eliminates flicker on repeated phrases.
   ~50 LOC, single file.
2. **`--single-line` overlay flag** — render only one Qt window for true
   subtitle-only mode. Currently both lines render even with
   `--no-translate`. ~30 LOC in `voci/overlay.py` + main.py wiring.
3. **Speech-translation models** (Meta SeamlessM4T or NVIDIA Canary-1B) —
   one-shot audio→Arabic instead of STT then MT. Architecturally bigger
   but fundamentally faster + word-order-aware. ~200 LOC + new model.
4. **Context-aware translation** — pass last 1-2 sentences as context to
   cloud LLM. Improves Arabic quality on context-dependent sentences.
   ~20 lines in `voci/translate/_worker.py`.

Lower priority is documented in README under "Known issues & future
improvements".

## Gotchas

- **Python 3.12 pinned** in `.python-version`. NeMo doesn't fully work
  on 3.13 yet (May 2026).
- **CUDA**: torch installed from cu124 index in pyproject.toml. NeMo +
  ctranslate2 both need CUDA 12.x runtime.
  `voci/__init__.py` preloads cuBLAS/cuDNN .so files for ctranslate2 to
  find them — do not delete this file.
- **`.venv.deepgram`** in repo root is a 9 GB backup of the original pip
  venv. User can `rm -rf` once they've confirmed everything works. Don't
  re-create it.
- **uv + ruff + pyright** are the toolchain. `pyproject.toml` is canonical.
  `requirements.txt` was deleted; don't recreate it.
- **PySide6 + NeMo type stubs are weak** — pyright `reportAttributeAccessIssue`
  is downgraded to warning so 67 false positives don't fail the build.
- **`pynput` global hotkeys are X11-best.** User is on Pop!_OS (X11
  default). Wayland would break the hotkey layer.

## Files NOT to touch unless purposefully modifying

- `voci/__init__.py` — CUDA library preload, fragile
- `voci/audio_capture.py`, `voci/mic_capture.py` — proven, stable
- `voci/overlay.py`, `voci/dictate_indicator.py` — Qt rendering, lots of
  PySide6 idioms; refactoring risks visual regressions
- `voci/typer.py` — clipboard injection, X11/Wayland quirks
- `.venv.deepgram/` — old venv backup, see Gotchas above

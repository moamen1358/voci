# voci

**Live subtitles for whatever's playing on your computer — in any language you want.**

Watch an English video, see Arabic subtitles float on top of it in real time.
Or any other target language. Runs on your own machine, no monthly cost,
no account needed.

<!-- TODO: drop a screenshot of the running overlay here. Save the file to
     docs/screenshot.png and replace this comment with:
     ![voci subtitle overlay](docs/screenshot.png) -->

---

## What you need

- A Linux laptop (tested on Pop!_OS / Ubuntu, should work on most distros)
- An NVIDIA graphics card (used for the speech recognition)
- About 2 GB of free disk space for the AI models
- A few minutes to install

---

## Install

Open a terminal in the folder where you want voci to live and run:

```bash
# 1. Install the system tools voci uses
sudo apt install pulseaudio-utils xdotool

# 2. Install uv (Python environment manager)
curl -LsSf https://astral.sh/uv/install.sh | sh

# 3. Clone voci and set up its environment
git clone https://github.com/moamen1358/STTL.git voci
cd voci
uv sync
```

That's it. The first `uv sync` takes a few minutes because it pulls in
PyTorch and the speech model dependencies.

---

## Use it

**Once, before your first run:** pick which sound source voci should listen
to (your speakers' output, usually):

```bash
uv run python scripts/list_audio_sources.py
```

It saves your choice and you don't need to do this again.

**Then, every time you want to run it:**

```bash
./run.sh --target ar
```

This shows English subtitles on the top line and Arabic on the bottom line.
Change `ar` to any 2-letter language code (`es` Spanish, `fr` French, `de`
German, `pt` Portuguese, `ja` Japanese, `zh` Chinese, etc.).

**The first run takes a couple of minutes** because it downloads the AI
models. After that it starts in about 15 seconds.

---

## Subtitle only (no translation)

If you just want English subtitles with no translation:

```bash
./run.sh --no-translate
```

Both overlay lines will show English.

---

## Hotkeys

While voci is running:

| Press | What happens |
|---|---|
| `Alt + Ctrl + V` | Show / hide the overlay |
| `Alt + Ctrl + D` | Let me drag the overlay with the mouse (toggle) |
| `Alt + Ctrl + X` | Clear the current text |
| `Alt + Ctrl + L` | Switch the target language |

---

## Stop it

In the terminal where you started voci, press **`Ctrl + C`**. That's the
clean way.

⚠️ **Don't press `Ctrl + Z`** — that suspends voci instead of killing it,
and the next time you start it you'll get a "CUDA out of memory" error
because the old one is still holding the graphics card.

If that happens, just run:

```bash
./run.sh kill
```

It cleans up everything.

---

## Something not working?

**Nothing is showing on screen when audio plays.** You probably skipped the
audio-source picker. Run:

```bash
uv run python scripts/list_audio_sources.py
```

and pick the source ending in `.monitor` (that's your speakers' output).

**It says "CUDA out of memory" when I start it.** A previous voci is still
holding GPU memory (you probably hit Ctrl-Z instead of Ctrl-C). Run:

```bash
./run.sh kill
```

then start again.

**The first run takes forever.** That's normal — it's downloading about
2 GB of AI models. You only pay this cost once. Subsequent launches take
~15 seconds.

**The translation is bad.** The default uses a free local translator
(OPUS-MT). For better Arabic quality you can plug in a cloud LLM like
Cerebras or Google Gemini — see `CLAUDE.md` and `./run.sh --help` for
the flags.

---

## Want more options?

Run `./run.sh --help` to see all the flags (different speech recognition
backends, different translation backends, hold-to-talk dictation mode,
etc.).

Developers and people who want to understand the architecture: see
[`CLAUDE.md`](CLAUDE.md).

---

## License

MIT.

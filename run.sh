#!/bin/bash
# voci launcher — sources API keys from ~/.config/voci/secrets.env then launches.
#
# Modes:
#   ./run.sh                       # subtitle overlay (default), target Arabic
#   ./run.sh --target en           # subtitle overlay, English only
#   ./run.sh --headless            # subtitle pipeline to stdout
#   ./run.sh dictate               # hold-to-talk dictation (default key F9)
#   ./run.sh dictate --target ar   # dictate with Arabic translation before typing
#   ./run.sh dictate --hotkey '<ctrl>+<alt>+space'
#   ./run.sh dictate --keyword Anthropic --keyword kubectl   # boost terms
set -e

cd "$(dirname "$0")"

if [[ ! -f .venv/bin/python ]]; then
    echo "ERROR: .venv not found. Create with: python3 -m venv .venv && .venv/bin/pip install -r requirements.txt" >&2
    exit 1
fi

if [[ -f "$HOME/.config/voci/secrets.env" ]]; then
    set -a
    # shellcheck disable=SC1091
    source "$HOME/.config/voci/secrets.env"
    set +a
fi

if [[ -z "${DEEPGRAM_API_KEY:-}" ]]; then
    echo "ERROR: DEEPGRAM_API_KEY not set. Put it in ~/.config/voci/secrets.env" >&2
    echo "  echo 'DEEPGRAM_API_KEY=...' > ~/.config/voci/secrets.env && chmod 600 ~/.config/voci/secrets.env" >&2
    exit 2
fi

mode="${1:-overlay}"
case "$mode" in
    dictate)
        shift
        exec .venv/bin/python -m voci.dictate "$@"
        ;;
    overlay|"")
        exec .venv/bin/python -m voci.main --show-on-start "$@"
        ;;
    *)
        # Forward unknown args to overlay mode
        exec .venv/bin/python -m voci.main --show-on-start "$@"
        ;;
esac

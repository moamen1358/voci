#!/bin/bash
# voci launcher — fully local STT + translation, runs Parakeet on the GPU.
#
# Modes:
#   ./run.sh                       # subtitle overlay (default), target Arabic
#   ./run.sh --target en           # subtitle overlay, English only (no translate)
#   ./run.sh --headless            # subtitle pipeline to stdout
#   ./run.sh dictate               # hold-to-talk dictation (default key F9)
#   ./run.sh dictate --target ar   # dictate with Arabic translation before typing
#   ./run.sh dictate --hotkey '<ctrl>+<alt>+space'
#
# First launch downloads ~1.8 GB of model weights to ~/.cache/huggingface/
# (Parakeet ~600 MB) and ~/.cache/voci/ (NLLB CT2 conversion ~700 MB).
set -e

cd "$(dirname "$0")"

if ! command -v uv >/dev/null 2>&1; then
    echo "ERROR: 'uv' not found in PATH. Install from https://github.com/astral-sh/uv" >&2
    exit 1
fi

# Verify CUDA before paying the model load cost
if ! uv run --no-sync python -c "import torch, sys; sys.exit(0 if torch.cuda.is_available() else 3)" 2>/dev/null; then
    echo "ERROR: CUDA not available. Parakeet needs an NVIDIA GPU." >&2
    echo "  Check: nvidia-smi" >&2
    exit 3
fi

# Optional secrets file is no longer required (no Deepgram key) but we still
# source it if present for forward compat with anything user-defined.
if [[ -f "$HOME/.config/voci/secrets.env" ]]; then
    set -a
    # shellcheck disable=SC1091
    source "$HOME/.config/voci/secrets.env"
    set +a
fi

mode="${1:-overlay}"
case "$mode" in
    dictate)
        shift
        exec uv run python -m voci.dictate "$@"
        ;;
    overlay|"")
        exec uv run python -m voci.main --show-on-start "$@"
        ;;
    *)
        # Forward unknown args to overlay mode
        exec uv run python -m voci.main --show-on-start "$@"
        ;;
esac

#!/bin/bash
# voci launcher — fully local STT + translation, runs Parakeet on the GPU.
#
# Modes:
#   ./run.sh                              # subtitle overlay (default), target Arabic
#   ./run.sh --target en                  # subtitle overlay, English only (no translate)
#   ./run.sh --headless                   # subtitle pipeline to stdout
#   ./run.sh --stt-backend deepgram \
#            --target en                  # Deepgram Nova-2 cloud STT (needs DEEPGRAM_API_KEY)
#   ./run.sh --stt-backend soniox \
#            --target ar                  # Soniox sub-200 ms cloud STT (needs SONIOX_API_KEY)
#   ./run.sh --translator cerebras \
#            --target ar                  # Cerebras Llama 3.1 8B for translation
#                                         # (1 M tokens/day free, needs CEREBRAS_API_KEY)
#   Keys live in ~/.config/voci/secrets.env (sourced automatically below).
#   ./run.sh dictate                      # hold-to-talk dictation (default key F9)
#   ./run.sh dictate --target ar          # dictate with Arabic translation before typing
#   ./run.sh dictate --hotkey '<ctrl>+<alt>+space'
#   ./run.sh status                       # show running voci processes + GPU usage
#   ./run.sh kill                         # terminate any running voci process (handles Ctrl-Z'd)
#
# First launch downloads ~1.8 GB of model weights to ~/.cache/huggingface/
# (Parakeet ~600 MB) and ~/.cache/voci/ (NLLB CT2 conversion ~700 MB).
set -e

cd "$(dirname "$0")"

mode="${1:-overlay}"

# ---- Process control subcommands (no CUDA / uv needed) ----

if [[ "$mode" == "status" ]]; then
    pids=$(pgrep -f 'voci\.(main|dictate)' || true)
    if [[ -z "$pids" ]]; then
        echo "No voci process running."
    else
        echo "Running voci processes:"
        # shellcheck disable=SC2086
        ps -o pid,stat,etime,command -p $(echo "$pids" | tr '\n' ',' | sed 's/,$//')
        echo
        echo "STAT codes: R=running, S=sleeping (normal), T=stopped (Ctrl-Z'd — won't free GPU until killed)"
    fi
    if command -v nvidia-smi >/dev/null 2>&1; then
        echo
        echo "GPU compute apps:"
        nvidia-smi --query-compute-apps=pid,process_name,used_memory --format=csv
        echo
        nvidia-smi --query-gpu=memory.used,memory.free,memory.total --format=csv,noheader
    fi
    exit 0
fi

if [[ "$mode" == "kill" ]]; then
    pids=$(pgrep -f 'voci\.(main|dictate)' || true)
    if [[ -z "$pids" ]]; then
        echo "No voci process running."
    else
        echo "Killing: $(echo "$pids" | tr '\n' ' ')"
        # SIGCONT first — stopped (Ctrl-Z'd) processes don't react to SIGTERM
        # until they've been resumed.
        pkill -CONT -f 'voci\.(main|dictate)' 2>/dev/null || true
        pkill -f 'voci\.(main|dictate)' 2>/dev/null || true
        sleep 1
        # SIGKILL any survivors
        if pgrep -f 'voci\.(main|dictate)' >/dev/null 2>&1; then
            echo "Forcing SIGKILL on survivors..."
            pkill -9 -f 'voci\.(main|dictate)' 2>/dev/null || true
            sleep 0.5
        fi
        if pgrep -f 'voci\.(main|dictate)' >/dev/null 2>&1; then
            echo "WARN: some processes still alive — may need manual investigation." >&2
        else
            echo "All voci processes terminated."
        fi
    fi
    if command -v nvidia-smi >/dev/null 2>&1; then
        echo
        nvidia-smi --query-gpu=memory.used,memory.free --format=csv,noheader
    fi
    exit 0
fi

# ---- Run modes (need uv + CUDA) ----

if ! command -v uv >/dev/null 2>&1; then
    echo "ERROR: 'uv' not found in PATH. Install from https://github.com/astral-sh/uv" >&2
    exit 1
fi

# Reduces CUDA allocator fragmentation when Parakeet + OPUS-MT both live on
# the GPU. Recommended by PyTorch's OOM error message; no downside.
export PYTORCH_CUDA_ALLOC_CONF="${PYTORCH_CUDA_ALLOC_CONF:-expandable_segments:True}"

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

#!/usr/bin/env bash
# One-shot setup for an Apple Silicon Mac: Homebrew packages, Python environment, model pulls.
# Re-runnable. Needs network once; afterwards raida runs offline.
#
#   --skip-llm             do not download the LLM named in raida.toml (it is already
#                          downloaded, or you run your own model server)
#   --skip-speech-models   do not download the Parakeet and Whisper weights (Apple speech only,
#                          or no access to Hugging Face)
set -euo pipefail

skip_llm=0
skip_speech=0
for arg in "$@"; do
  case "$arg" in
    --skip-llm) skip_llm=1 ;;
    --skip-speech-models) skip_speech=1 ;;
    *) echo "usage: $0 [--skip-llm] [--skip-speech-models]" >&2; exit 2 ;;
  esac
done

cd "$(dirname "$0")/.."

if [[ "$(uname -s)" != "Darwin" || "$(uname -m)" != "arm64" ]]; then
  echo "This script targets Apple Silicon macOS. On other platforms run: uv sync --all-groups" >&2
  exit 1
fi

# Use the Apple Silicon Homebrew explicitly. Macs that also carry the Intel Homebrew under
# /usr/local usually list it first on PATH; its packages are x86_64, and MLX cannot run on an
# x86_64 Python.
BREW=/opt/homebrew/bin/brew
if [[ ! -x "$BREW" ]]; then
  echo "Apple Silicon Homebrew not found at /opt/homebrew. Install it: https://brew.sh" >&2
  exit 1
fi
export PATH="/opt/homebrew/bin:$PATH"

echo "==> Homebrew packages (ffmpeg, pango for PDF export, uv, llama.cpp, ollama)"
"$BREW" list ffmpeg    >/dev/null 2>&1 || "$BREW" install ffmpeg
"$BREW" list pango     >/dev/null 2>&1 || "$BREW" install pango
"$BREW" list uv        >/dev/null 2>&1 || "$BREW" install uv
# llama-server answers; Ollama downloads the weights, which llama-server reads in place.
"$BREW" list llama.cpp >/dev/null 2>&1 || "$BREW" install llama.cpp
if ! command -v ollama >/dev/null 2>&1; then
  # The formula (not the auto-updating app cask) keeps the install reproducible and offline.
  "$BREW" install ollama
fi

echo "==> Python environment (Python 3.12, pinned dependencies, Apple extras)"
uv python install 3.12 >/dev/null
uv sync --all-groups --extra mac

if [[ ! -f raida.toml ]]; then
  cp raida.example.toml raida.toml
  echo "==> Created raida.toml from the example. Edit llm.model if you want a different model."
fi

MODEL="$(uv run python -c 'from raida.config import load_config; print(load_config().llm.model)')"

if (( ! skip_llm )); then
  echo "==> Starting Ollama (if not running) with settings for this app"
  export OLLAMA_NUM_PARALLEL=1 OLLAMA_KEEP_ALIVE=1h OLLAMA_FLASH_ATTENTION=1 OLLAMA_KV_CACHE_TYPE=q8_0
  export OLLAMA_NO_CLOUD=1
  if ! curl -fsS http://127.0.0.1:11434/api/tags >/dev/null 2>&1; then
    nohup ollama serve >/tmp/ollama-serve.log 2>&1 &
    sleep 2
  fi
fi

# Written out rather than built as an array: macOS ships bash 3.2, where an empty array
# expanded under `set -u` is an error.
if (( skip_llm && skip_speech )); then
  echo "==> Skipping model downloads (--skip-llm --skip-speech-models)"
elif (( skip_llm )); then
  echo "==> Pulling speech-to-text weights (one time, needs network)"
  ./scripts/pull-models.sh --skip-llm
elif (( skip_speech )); then
  echo "==> Pulling the LLM (one time, needs network)"
  ./scripts/pull-models.sh --skip-speech-models "$MODEL"
else
  echo "==> Pulling models (one time, needs network)"
  ./scripts/pull-models.sh "$MODEL"
fi

MEM_GB=$(( $(sysctl -n hw.memsize) / 1024 / 1024 / 1024 ))
if (( MEM_GB <= 96 )); then
  cat <<MSG

Note: this Mac has ${MEM_GB} GB unified memory. macOS caps GPU-allocatable memory at roughly
two thirds to three quarters of RAM. For a 60 GB+ model with a long context, raise the cap
(resets on reboot):

  sudo sysctl iogpu.wired_limit_mb=$(( (MEM_GB - 12) * 1024 ))

MSG
fi

echo "==> Environment check"
export DYLD_FALLBACK_LIBRARY_PATH="/opt/homebrew/lib:${DYLD_FALLBACK_LIBRARY_PATH:-}"
uv run raida doctor || true

cat <<MSG

Done. Start the model server and the app, each in its own terminal:

  make llm MODEL=${MODEL}   # llama-server on the weights Ollama downloaded
  make dev                  # the app, at http://127.0.0.1:8765

With llm.backend = "ollama" in raida.toml, run \`make ollama\` instead of \`make llm\`. With your
own model server, start it at llm.base_url and run only \`make dev\`.
MSG

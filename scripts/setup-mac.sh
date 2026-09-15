#!/usr/bin/env bash
# One-shot setup for an Apple Silicon Mac: Homebrew packages, Python environment, model pulls.
# Re-runnable. Needs network once; afterwards raida runs offline.
set -euo pipefail

cd "$(dirname "$0")/.."

if [[ "$(uname -s)" != "Darwin" || "$(uname -m)" != "arm64" ]]; then
  echo "This script targets Apple Silicon macOS. On other platforms run: uv sync --all-groups" >&2
  exit 1
fi

if ! command -v brew >/dev/null 2>&1; then
  echo "Homebrew is required: https://brew.sh" >&2
  exit 1
fi

echo "==> Homebrew packages (ffmpeg, pango for PDF export, uv, ollama)"
brew list ffmpeg >/dev/null 2>&1 || brew install ffmpeg
brew list pango  >/dev/null 2>&1 || brew install pango
brew list uv     >/dev/null 2>&1 || brew install uv
if ! command -v ollama >/dev/null 2>&1; then
  # The formula (not the auto-updating app cask) keeps the install reproducible and offline.
  brew install ollama
fi

echo "==> Python environment (Python 3.12, pinned dependencies, Apple extras)"
uv python install 3.12 >/dev/null
uv sync --all-groups --extra mac

if [[ ! -f raida.toml ]]; then
  cp raida.example.toml raida.toml
  echo "==> Created raida.toml from the example. Edit llm.model if you want a different model."
fi

MODEL="$(uv run python -c 'from raida.config import load_config; print(load_config().llm.model)')"

echo "==> Starting Ollama (if not running) with settings for this app"
export OLLAMA_NUM_PARALLEL=1 OLLAMA_KEEP_ALIVE=1h OLLAMA_FLASH_ATTENTION=1 OLLAMA_KV_CACHE_TYPE=q8_0
if ! curl -fsS http://127.0.0.1:11434/api/tags >/dev/null 2>&1; then
  nohup ollama serve >/tmp/ollama-serve.log 2>&1 &
  sleep 2
fi

echo "==> Pulling models (one time, needs network)"
./scripts/pull-models.sh "$MODEL"

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

Done. Start the app with:

  make dev          # or: uv run raida serve

Ollama must be running (`ollama serve`) whenever raida runs.
MSG

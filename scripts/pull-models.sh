#!/usr/bin/env bash
# Pull the LLM into Ollama and the transcription weights into the app's Hugging Face cache.
# Usage: scripts/pull-models.sh [--skip-llm] [--skip-speech-models] [ollama-model-tag]
#   --skip-llm             leave the LLM alone (it is already downloaded, or another server runs it)
#   --skip-speech-models   leave out Parakeet and Whisper (Apple speech only, or no Hugging Face access)
set -euo pipefail
cd "$(dirname "$0")/.."

skip_llm=0
skip_speech=0
model_arg=""
for arg in "$@"; do
  case "$arg" in
    --skip-llm) skip_llm=1 ;;
    --skip-speech-models) skip_speech=1 ;;
    -*) echo "usage: $0 [--skip-llm] [--skip-speech-models] [ollama-model-tag]" >&2; exit 2 ;;
    *) model_arg="$arg" ;;
  esac
done

if (( ! skip_llm )); then
  MODEL="${model_arg:-$(uv run python -c 'from raida.config import load_config; print(load_config().llm.model)')}"
  echo "==> ollama pull ${MODEL}"
  ollama pull "${MODEL}"
fi

if (( ! skip_speech )); then
  echo "==> Transcription weights (Parakeet TDT 0.6B v3, Whisper large-v3 MLX)"
  RAIDA_TRANSCRIBE__ALLOW_MODEL_DOWNLOAD=true uv run python - <<'PY'
from huggingface_hub import snapshot_download

from raida.config import load_config
from raida.model_env import apply_model_env

config = load_config()
apply_model_env(config)
for repo in (config.transcribe.parakeet_model, config.transcribe.whisper_model):
    path = snapshot_download(repo)
    print(f"{repo} -> {path}")
PY
fi

if (( ! skip_llm )); then
  echo "==> Verifying with ollama ps after a warm-up request"
  curl -fsS http://127.0.0.1:11434/api/generate -d "{\"model\":\"${MODEL}\",\"prompt\":\"OK\",\"stream\":false,\"options\":{\"num_ctx\":74240}}" >/dev/null
  ollama ps
  echo "Check the CONTEXT column is at least 74240 and PROCESSOR reads 100% GPU."
  # `make llm` loads the weights again in llama-server; unloading Ollama's copy keeps the two
  # from holding a large model in memory at the same time. Ollama reloads it on its next request.
  ollama stop "${MODEL}"
fi

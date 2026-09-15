#!/usr/bin/env bash
# Pull the LLM into Ollama and the transcription weights into the app's Hugging Face cache.
# Usage: scripts/pull-models.sh [ollama-model-tag]
set -euo pipefail
cd "$(dirname "$0")/.."

MODEL="${1:-$(uv run python -c 'from raida.config import load_config; print(load_config().llm.model)')}"

echo "==> ollama pull ${MODEL}"
ollama pull "${MODEL}"

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

echo "==> Verifying with ollama ps after a warm-up request"
curl -fsS http://127.0.0.1:11434/api/generate -d "{\"model\":\"${MODEL}\",\"prompt\":\"OK\",\"stream\":false,\"options\":{\"num_ctx\":74240}}" >/dev/null
ollama ps
echo "Check the CONTEXT column is at least 74240 and PROCESSOR reads 100% GPU."

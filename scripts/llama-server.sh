#!/usr/bin/env bash
# Start llama.cpp's llama-server with the settings raida expects (docs/model-setup.md).
#
#   scripts/llama-server.sh --ollama qwen3:30b-a3b    reuse weights Ollama already downloaded
#   scripts/llama-server.sh --gguf /path/model.gguf    any GGUF file
#
# --ollama works when Ollama stored the model as a standard GGUF (verified for qwen3:30b-a3b).
# Models Ollama keeps in its own layout may not load; use the publisher's GGUF with --gguf.
#
# Options: --alias NAME (default: the Ollama tag or the file name; must equal llm.model),
# --port N (8080). Environment overrides: RAIDA_LLAMA_CTX (81920 tokens shared by all slots;
# must cover llm.synthesis_budget_tokens + output_reserve_tokens + prompt_overhead_tokens),
# RAIDA_LLAMA_SLOTS (3), RAIDA_LLAMA_CACHE_MIB (8192, RAM kept for prompts of idle sessions),
# RAIDA_LLAMA_THINK_BUDGET (1024 reasoning tokens per answer, about 25 s at 25k context on an
# M2 Max; -1 unlimited),
# RAIDA_LLAMA_IDLE_S (3600: unload the model after an hour without requests; the next request
# loads it again in seconds).
set -euo pipefail

gguf=""
alias_name=""
port=8080
while [[ $# -gt 0 ]]; do
  case "$1" in
    --ollama)
      tag="$2"; shift 2
      name="${tag%%:*}"; version="${tag#*:}"; [[ "$tag" == *:* ]] || version="latest"
      manifest="$HOME/.ollama/models/manifests/registry.ollama.ai/library/$name/$version"
      [[ -f "$manifest" ]] || { echo "error: no Ollama model $tag ($manifest)" >&2; exit 2; }
      digest=$(/usr/bin/python3 -c 'import json,sys; m=json.load(open(sys.argv[1])); print(next(l["digest"] for l in m["layers"] if l["mediaType"].endswith(".model")))' "$manifest")
      gguf="$HOME/.ollama/models/blobs/${digest/:/-}"
      [[ -n "$alias_name" ]] || alias_name="$tag"
      ;;
    --gguf) gguf="$2"; shift 2 ;;
    --alias) alias_name="$2"; shift 2 ;;
    --port) port="$2"; shift 2 ;;
    *) echo "usage: $0 (--ollama TAG | --gguf PATH) [--alias NAME] [--port N]" >&2; exit 2 ;;
  esac
done
[[ -n "$gguf" && -f "$gguf" ]] || { echo "error: give --ollama TAG or --gguf PATH to an existing file" >&2; exit 2; }
[[ -n "$alias_name" ]] || alias_name="$(basename "$gguf" .gguf)"

server="$(command -v llama-server || true)"
[[ -x /opt/homebrew/bin/llama-server ]] && server=/opt/homebrew/bin/llama-server
[[ -n "$server" ]] || { echo "error: llama-server not found; run: /opt/homebrew/bin/brew install llama.cpp" >&2; exit 2; }

# f16 KV cache: q8_0 halves its memory but decodes about 25% slower on Apple GPUs (measured
# 73 vs 56 tok/s at 4k context with a 30B MoE on an M2 Max). --kv-unified shares one KV pool
# between slots; --cache-ram keeps the prompts of idle slots, so each session's sources are
# read once. Batches of 2048 read prompts 5 to 8% faster than the default 512.
exec "$server" \
  -m "$gguf" --alias "$alias_name" --host 127.0.0.1 --port "$port" --jinja --no-webui \
  -c "${RAIDA_LLAMA_CTX:-81920}" -np "${RAIDA_LLAMA_SLOTS:-3}" --kv-unified \
  -fa on -b 2048 -ub 2048 \
  --cache-ram "${RAIDA_LLAMA_CACHE_MIB:-8192}" \
  --reasoning-budget "${RAIDA_LLAMA_THINK_BUDGET:-1024}" \
  --reasoning-budget-message "Enough thinking; I will now write the answer." \
  --sleep-idle-seconds "${RAIDA_LLAMA_IDLE_S:-3600}"

# Benchmarks

Machine-specific measurements appended by `scripts/bench_llm.py` and `scripts/bench_stt.py`
(`make bench`). Record the model tag, server and macOS version with each run. Use the prefill
rate at 32k and 64k tokens to decide `llm.synthesis_budget_tokens`, and compare Ollama's MLX
and GGUF tags, or llama-server, on the same prompt sizes before settling on one.

Reference points from public sources (not this machine), September 2026:

| Setup | Measurement | Source |
| --- | --- | --- |
| llama.cpp, Llama-7B F16, M4 Max 40-core | prefill 923 tok/s, decode 31.6 tok/s | ggml-org/llama.cpp discussion 4167 |
| llama.cpp, Llama-7B F16, M5 Max 40-core | prefill 3158 tok/s, decode 37.1 tok/s | same |
| gpt-oss-120b MXFP4 on M5 Max 128 GB via MLX | ~88 tok/s decode, ~2.7k tok/s prefill at 16k ctx | siliconscore.com (secondary) |
| Parakeet TDT 0.6B v3 CoreML, M4 Pro | 155x real time | FluidAudio benchmarks |
| mlx-whisper large-v3-turbo, M4 Pro | ~14-18x real time | secondary blog reports |

## 2026-09-25, M2 Max 96 GB, macOS 26.6.2, Ollama 0.30.10, `qwen3:30b-a3b` (Q4_K_M, thinking-only tag)

Settings: OLLAMA_FLASH_ATTENTION=1, OLLAMA_KV_CACHE_TYPE=q8_0, num_ctx 138432, model 100% GPU.
Prompt: one Traditional Chinese transcript of a 2h20m recording (31,259 characters).

| Measurement | Value |
| --- | --- |
| Tokens for that transcript | 22,842 to 25,431 (with and without anchors) |
| Characters per token | 1.37 (0.83 tokens per CJK character) |
| Prefill, default `num_batch` 512 | 213 tok/s |
| Prefill, `num_batch` 1024 | 222 tok/s |
| Prefill, `num_batch` 2048 | 216 tok/s |
| Prefill, four transcripts (~100k tokens) | did not finish in 900 s |
| Prefill, cached prompt prefix (same sources, new question) | 0.6 s |
| Decode, short English prompt | 62 to 74 tok/s |
| Decode, this model's reasoning before an answer | a few hundred to several thousand tokens per answer |

Conclusions: batch size is not the lever for prefill on this stack; prompt-prefix caching is, so
ask several questions in one session rather than one question per session. Reading 100k tokens
takes over 15 minutes and the first answer over four two-hour recordings costs 20 to 25 minutes;
one recording per session answers in about 5 minutes. Apple SpeechAnalyzer via yap transcribed
2h20m of Mandarin in about 60 s (about 140x real time).

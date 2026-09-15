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

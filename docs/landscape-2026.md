# Local models and tools landscape, September 2026

Research digest that informed the design. Claims marked (P) were verified from primary sources
(project docs, source files, benchmark CSVs); (S) come from secondary reports and should be
re-checked before being relied on. Many vendor sites were unreachable from the research
environment, which is why several items are (S).

## Can everything run locally and offline? Yes.

- Text LLMs: open-weight models with 128k-262k context run on Apple Silicon through Ollama,
  llama.cpp, MLX or LM Studio. Current families: OpenAI gpt-oss-20b/120b (Apache-2.0, 131k
  context, P), Qwen 3.5/3.6/3.8 (Apache-2.0, 262k context, P for 3.6/3.8), Google Gemma 4
  (Apache-2.0, 128k-256k, S), Mistral Small 4 (119B MoE, Apache-2.0, S), IBM Granite 4.2,
  NVIDIA Nemotron 3 (NVIDIA Open Model License, S), Meta Llama 4 (Meta license). Frontier-size
  open models (Kimi K3, GLM-5.2, DeepSeek V4) exceed any laptop.
- Speech-to-text: NVIDIA Parakeet TDT 0.6B v3 (CC-BY-4.0), Whisper large-v3 (MIT), Cohere
  Transcribe 03-2026 (Apache-2.0, gated), Qwen3-ASR (Apache-2.0), Voxtral Mini 4B Realtime
  (Apache-2.0), Kyutai STT, Moonshine, Granite Speech, all with Apple Silicon runtimes.
- OCR: Apple Vision (built in), Tesseract, Docling, vision LLMs.
- Everything else (PDF parsing, ffmpeg, PDF export) is ordinary open-source software.

## LLM runtimes on macOS (P unless noted)

| Runtime | License | OpenAI-style API | Metal | Notes |
| --- | --- | --- | --- | --- |
| Ollama 0.34 | MIT | yes (`/v1`) plus native API | yes; MLX engine on Apple Silicon via `-mlx` tags | VRAM-tiered default context (>= 48 GiB -> 256k); `OLLAMA_NUM_PARALLEL` default 1; `ollama ps` shows context and GPU share; some `-mlx` tags ignore num_ctx (open issue). Install the formula, not the auto-updating app cask. |
| llama.cpp llama-server | MIT | yes | yes | Flash attention auto, per-slot KV quant, JSON-schema output, prompt cache save/restore, continuous batching, Unix socket binding. |
| mlx-lm server | MIT | yes | yes | "Not recommended for production"; quantized KV disables batching. Fastest at short context. |
| LM Studio | proprietary, free | yes | yes | Bundles llama.cpp and MLX. |
| vllm-metal | unclear | yes | yes | Newest; community plugin. |
| GPT4All | MIT | yes | yes | No commits since May 2025; avoid. |
| Docker Desktop / OrbStack / Colima | - | - | no GPU in containers | Ollama FAQ: no GPU acceleration for Docker Desktop on macOS. Docker Model Runner runs llama.cpp on the host instead. |

Performance notes: MLX is 1.5-2x faster than llama.cpp for small models at short context (P for
one M4 Max measurement); the gap narrows above ~27B where memory bandwidth dominates (S); MLX is
reported ~50% slower than llama.cpp with flash attention above ~30k context (S, unverified,
decision-relevant, hence `make bench`). M5 Max prefill is ~3.4x M4 Max at equal GPU cores (P).

Long context: benchmark suites (Fiction.LiveBench, RULER) show most open models degrading
between 16k and 64k tokens even when 128k-1M is advertised (S). raida budgets 64k by default.

## Speech-to-text (P from the Open ASR Leaderboard repository CSVs, March 2026 snapshot)

English long-form (hour-long recordings), average word error rate and relative throughput:

| Model | WER | RTFx | License |
| --- | --- | --- | --- |
| Cohere Transcribe 03-2026 (2B) | 9.73 | 418 | Apache-2.0 (gated download) |
| Parakeet TDT 0.6B v3 | 10.72 | 1003 | CC-BY-4.0 |
| Whisper large-v3-turbo | 11.01 | 148 | MIT |
| Whisper large-v3 | 11.23 | 69 | MIT |
| Canary-Qwen 2.5B | 11.20 | 16 | CC-BY-4.0 |

Multilingual (de/fr/it/es/pt): Parakeet v3 ties Whisper large-v3 at 4.81 WER with 15x the
throughput; Whisper's advantage is breadth (99 languages). Parakeet is a transducer and does not
hallucinate text into silence, Whisper's classic long-audio failure.

Runtimes measured on one M4 MacBook Pro, same clip (P, mac-whisper-speedtest): FluidAudio
CoreML Parakeet 0.19 s, parakeet-mlx 0.50 s, mlx-whisper 1.02 s, whisper.cpp+CoreML 1.23 s,
WhisperKit 2.22 s, faster-whisper (CPU only on Mac) 6.96 s. `mlx-audio` 0.5.4 (MIT) is a single
runtime covering Parakeet, Whisper, Qwen3-ASR, Cohere Transcribe and more with an
OpenAI-compatible `/v1/audio/transcriptions` server.

Apple's on-device SpeechAnalyzer (macOS 26): about 30 locales, very fast (a 34-minute video in
~45 s vs ~1 min 41 s for Whisper large-v3-turbo, S), competitive English accuracy, no speaker
separation, needs an explicit locale. CLI wrappers: `yap` (CC0), `ohr` (MIT, OpenAI-compatible
server), `speech-analyzer-cli`.

Diarization (not in v1): senko (MIT, CoreML, 1 h in ~8 s on M3), FluidAudio (10.6% DER on
AMI-SDM at 323x), pyannote 4.x (Hugging Face token once, then offline). 2026 models that
diarize natively: MOSS-Transcribe-Diarize 0.9B, Granite Speech 4.1 2B-plus, VibeVoice-ASR.

## Documents, export, app shell

- PDF text: pymupdf4llm (AGPL, best markdown, fast) vs pypdfium2 (permissive, plain text) vs
  Docling (MIT, heavy torch, strong layout/tables) vs marker/MinerU (restricted weights).
- OCR: Apple Vision via ocrmac (no downloads); Tesseract/OCRmyPDF (MPL); Docling OcrMac option.
- Markdown to PDF: WeasyPrint 70 (BSD, needs Pango) vs fpdf2 (pure pip, basic) vs PyMuPDF Story
  (AGPL) vs headless Chromium (heavy) vs pandoc+typst (GPL binary).
- App shell: FastAPI + SSE + static UI now; Tauri v2 later for a .app with real drop paths.
  Rapid Python UIs (Gradio, Streamlit, Chainlit, NiceGUI) were rejected for a product-grade,
  parallel-job UI.
- Existing apps (Open WebUI, AnythingLLM, LM Studio, Open Notebook, Khoj) are retrieval-chat
  products; none does whole-corpus synthesis with export, hence build rather than fork.

## Sources

- Ollama docs (context-length, faq, api, openai-compatibility, macos, gpu):
  https://github.com/ollama/ollama/tree/main/docs
- Ollama Homebrew formula (mlx-c dependency): https://github.com/Homebrew/homebrew-core/blob/main/Formula/o/ollama.rb
- Ollama MLX context bug: https://github.com/ollama/ollama/issues/16586
- llama.cpp server README: https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md
- llama.cpp Apple Silicon benchmarks: https://github.com/ggml-org/llama.cpp/discussions/4167
- llama.cpp native vs container on Mac: https://github.com/ggml-org/llama.cpp/discussions/8042
- mlx-lm server docs: https://github.com/ml-explore/mlx-lm/blob/main/mlx_lm/SERVER.md
- vllm-metal: https://github.com/vllm-project/vllm-metal
- OrbStack GPU request: https://github.com/orbstack/orbstack/issues/1818
- Qwen3.8 / Qwen3.6: https://github.com/QwenLM/Qwen3.8 https://github.com/QwenLM/Qwen3.6
- gpt-oss: https://openai.com/index/introducing-gpt-oss/
- Open ASR Leaderboard data: https://github.com/huggingface/open_asr_leaderboard/tree/main/scripts/data
- Parakeet TDT 0.6B v3: https://huggingface.co/nvidia/parakeet-tdt-0.6b-v3
- parakeet-mlx: https://github.com/senstella/parakeet-mlx
- mlx-whisper: https://pypi.org/project/mlx-whisper/
- mlx-audio: https://github.com/Blaizzy/mlx-audio
- whisper.cpp: https://github.com/ggml-org/whisper.cpp
- FluidAudio benchmarks: https://github.com/FluidInference/FluidAudio/blob/main/Documentation/Benchmarks.md
- mac-whisper-speedtest: https://github.com/anvanvan/mac-whisper-speedtest
- faster-whisper has no Metal backend: https://github.com/SYSTRAN/faster-whisper/issues/911
- yap: https://github.com/finnvoor/yap ; ohr: https://github.com/Arthur-Ficial/ohr
- senko: https://github.com/narcotic-sh/senko ; pyannote: https://github.com/pyannote/pyannote-audio
- pymupdf4llm: https://github.com/pymupdf/pymupdf4llm ; PyMuPDF license: https://github.com/pymupdf/PyMuPDF
- pypdfium2: https://pypi.org/project/pypdfium2/ ; Docling: https://github.com/docling-project/docling
- ocrmac: https://github.com/straussmaximilian/ocrmac
- WeasyPrint: https://pypi.org/project/weasyprint/ ; fpdf2: https://pypi.org/project/fpdf2/
- Tauri v2 Python sidecar example: https://github.com/dieharders/example-tauri-v2-python-server-sidecar
- Open WebUI license: https://github.com/open-webui/open-webui/blob/main/LICENSE
- AnythingLLM local transcription: https://docs.anythingllm.com/setup/transcription-model-configuration/local/built-in
- Python free-threading status: https://docs.python.org/3/howto/free-threading-python.html

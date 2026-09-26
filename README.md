# raida

raida is an offline harness for turning a pile of sources into one piece of writing. Drop in
text, Markdown, PDF (including scanned pages), Word documents, subtitle files, audio and video;
raida extracts or transcribes them in parallel, you type an instruction such as "summarize these
into one article", a local model writes the answer while you watch, and you export it as txt,
md, pdf or docx. Nothing leaves the machine: the LLM, speech-to-text and OCR all run on the Mac.

Target machine: Apple Silicon MacBook Pro with 96-128 GB unified memory (smaller machines work
with smaller models; see `docs/model-setup.md`). The app is a local web app: one Python process
serves the UI at `http://127.0.0.1:8765`.

## How it works

```
 browser UI  <-- SSE events / JSON API -->  raida (FastAPI, asyncio)
   drop files                                  |
   instruction                                 |-- cpu pool:   PDF text, OCR (Apple Vision), docx, subtitles
   streamed answer                             |-- gpu slot:   transcription (Parakeet / Whisper on MLX)
   export buttons                              |-- llm slot:   Ollama or any OpenAI-compatible server
                                               |-- ffmpeg:     audio extraction from audio/video files
                                               `-- SQLite + files under the data directory
```

Sources are processed concurrently within per-resource limits: several PDFs parse on the CPU
while one file transcribes on the GPU. Every processed file is cached by content hash, so the
same file is never transcribed twice. When the combined sources exceed the configured context
budget, raida condenses each source with the instruction in mind, then writes the final answer
from the condensed notes (map-reduce); otherwise it puts everything into one prompt.

New here? `docs/user-guide.md` walks through installing, starting and using the app without
reading the rest of this file.

Design and decisions: `docs/architecture.md`, `docs/adr/`. Model choices: `docs/model-setup.md`.
Research behind the choices, with sources: `docs/landscape-2026.md`.

## Prerequisites

- macOS 14 or newer on Apple Silicon (M1 or later). macOS 15+ recommended.
- [Homebrew](https://brew.sh).
- Network access once, to install packages and download models. Afterwards raida runs offline.
  Hosts involved: `github.com` and `ghcr.io` (Homebrew), `pypi.org` and
  `files.pythonhosted.org` (Python packages), `ollama.com` and `registry.ollama.ai` (LLM
  weights), `huggingface.co`, `cdn-lfs.hf.co` and `*.xethub.hf.co` (transcription weights).
  Managed corporate networks often filter the model hosts while leaving PyPI open; ask for an
  exception, or pull on another network and copy the files (`docs/model-setup.md`, "Offline
  distribution"). Until then the `apple` transcription backend and any model already in
  Ollama keep the app usable.

## Getting started

```bash
git clone <this repo> raida && cd raida
./scripts/setup-mac.sh        # Homebrew deps (ffmpeg, pango, uv, ollama), Python env, models
make doctor                   # every line should read [OK  ]
make dev                      # starts the server and opens the browser
```

`scripts/setup-mac.sh` copies `raida.example.toml` to `raida.toml`, installs the pinned Python
environment with `uv`, starts Ollama, pulls the configured LLM (default `gpt-oss:120b`, about
65 GB) and downloads the transcription weights (about 6 GB). The pulls are the slow part.

Ollama has to be running whenever raida runs: `make ollama` in a second terminal, or the setup
script's background instance. Both apply these settings: one request at a time, one hour
keep-alive, flash attention on, 8-bit KV cache, cloud features off.

## Using the app

1. Drop files onto the left pane or click "Choose files". Very large local videos can be
   referenced in place with "Add by path" (folders must be listed under `paths.allowed_roots`).
2. Each source shows its stage (extracting, decoding audio, detecting language, transcribing,
   OCR) and a progress bar. Failed sources show the reason and a Re-run button. "View text"
   shows exactly what the model will read, with `[p. N]` page anchors or `[hh:mm:ss]` time codes.
3. Type an instruction and press Run (or Cmd+Enter). If sources are still processing, the run
   waits for them; tick "Run now with ready sources only" to skip waiting.
4. The answer streams in as Markdown. Export it as .txt, .md, .pdf or .docx, or copy it.
5. Sessions keep their sources, chat and exports; switch or create sessions from the top bar.

Languages: sources default to automatic language detection. Parakeet handles 25 European
languages; anything else routes to Whisper large-v3. Pick a language explicitly per source or
for all new sources when detection guesses wrong. The `apple` backend has no detector of its
own: choose the language under "Language for new sources" before adding media, otherwise the
source fails with a message saying so.

## Configuration

`raida.toml` (see `raida.example.toml` for every key and its default). Only `llm.model` is
required; the app refuses to start without it. Any key can be overridden with an environment
variable `RAIDA_<SECTION>__<KEY>`, for example `RAIDA_LLM__MODEL=gemma4:31b`.

| Key | Default | Meaning |
| --- | --- | --- |
| `llm.backend` | `ollama` | `ollama` (native API) or `openai_compatible` (llama-server, LM Studio, mlx_lm.server) |
| `llm.base_url` | `http://127.0.0.1:11434` | model server |
| `llm.model` | required | model name as the server knows it |
| `llm.synthesis_budget_tokens` | `64000` | inputs above this are condensed first |
| `llm.think` | unset | Ollama only: `false` turns off reasoning for hybrid models with a non-thinking mode (Qwen3 2504 tags, Qwen3.5/3.6); `"low"`, `"medium"` or `"high"` sets the effort for gpt-oss. Leave unset for thinking-only tags such as Qwen3 Thinking-2507, which otherwise leak reasoning into the answer |
| `transcribe.backend` | `parakeet` | `parakeet`, `whisper`, `apple` (macOS 26+, needs `brew install yap`) |
| `transcribe.allow_model_download` | `false` | set `true` only while fetching weights |
| `ocr.languages` | `["en-US"]` | Apple Vision language preference for scanned pages |
| `pdf.extractor` | `pymupdf4llm` | or `pypdfium2` for a permissive-license-only stack |
| `paths.data_dir` | `~/Library/Application Support/raida` | uploads, processed text, exports, SQLite, model cache |
| `paths.allowed_roots` | `[]` | folders allowed for "Add by path" |
| `workers.cpu` / `gpu` / `llm` | `auto` / `1` / `1` | concurrency per resource class |
| `export.pdf_renderer` | `auto` | `weasyprint` (best), `fpdf2` (no Homebrew libs needed) |

## Command line

```bash
uv run raida doctor                       # environment and model checks
uv run raida serve [--port N] [--no-open] # the web app
uv run raida process a.pdf b.mp3          # headless: ingest and print processed text
uv run raida ask <session-id> "instruction"
uv run raida export <message-id> --format pdf --output out.pdf
```

## Development

```bash
uv sync --all-groups --extra bundled-ffmpeg   # Linux or macOS without Homebrew ffmpeg
make lint                                     # ruff check + format check
make test                                     # pytest with fake LLM and fake transcriber
make bench                                    # prefill/decode and transcription timings -> docs/benchmarks.md
```

Tests run on Linux CI without MLX or Ollama: Apple-only imports are lazy, and the `fake` LLM
and transcriber backends stand in. Set `RAIDA_LLM__BACKEND=fake RAIDA_TRANSCRIBE__BACKEND=fake`
to run the whole app without any model. Logs are JSON lines on stdout.

Layout: `src/raida/api` (routes, SSE), `src/raida/pipeline` (scheduler, stages),
`src/raida/transcribe`, `src/raida/llm`, `src/raida/export`, `src/raida/web` (static UI),
`tests/`, `scripts/`, `docs/`.

## Troubleshooting

- `doctor` says the model is not pulled: `ollama pull <model>`, then check `ollama ps` after a
  request shows `CONTEXT` at least 74240 and `PROCESSOR 100% GPU`.
- Partial CPU offload or slow first token: the model plus context does not fit the GPU memory
  cap. Raise it with `sudo sysctl iogpu.wired_limit_mb=<MB>` (resets on reboot) or pick a
  smaller model.
- PDF export falls back to fpdf2: install `pango` with Homebrew. `make dev` and `make doctor`
  set `DYLD_FALLBACK_LIBRARY_PATH=/opt/homebrew/lib` so WeasyPrint finds it; export the same
  variable when calling `uv run raida serve` directly.
- Scanned PDF fails with an OCR message: OCR uses Apple Vision and needs `uv sync --extra mac`.
- A source is stuck: cancel it and re-run; the cache means finished work is not repeated.
- Media with no speech (silence, music, a test tone) becomes a ready source with an empty
  transcript, not a failure.
- A folder literally named `~` appeared next to the app: an earlier build did not expand the
  default data directory. Delete that folder; data now lives under
  `~/Library/Application Support/raida` as documented (`raida doctor` prints the path).
- The answer takes a long time to start, or is cut off, with a reasoning model: the model thinks
  first, raida shows only the final answer, and thinking counts against `num_predict`. Raise
  `llm.output_reserve_tokens` (16384 works for Qwen3 Thinking-2507), or set `llm.think = false`
  for a hybrid model that has a non-thinking mode. Reasoning text inside the answer, ending in
  `</think>`, means `think = false` was set for a thinking-only tag: remove it.
- `ollama pull` or `scripts/pull-models.sh` fail with TLS or "socket is not connected" errors
  while `uv sync` works: a network filter is blocking the model hosts listed under
  Prerequisites. Meanwhile set `transcribe.backend = "apple"` (macOS 26+, `brew install yap`)
  and `llm.model` to a model `ollama list` already shows.
- `make dev` says `uv: command not found`, or `which brew` prints `/usr/local/bin/brew` on an
  Apple Silicon Mac: `/opt/homebrew/bin` is not on your shell's PATH, or the Intel Homebrew
  (x86_64 packages) comes first. The Makefile and `scripts/setup-mac.sh` put `/opt/homebrew/bin`
  first themselves; when calling `uv` or `brew` by hand, use `/opt/homebrew/bin/uv` and
  `/opt/homebrew/bin/brew`, or add `export PATH="/opt/homebrew/bin:$PATH"` to `~/.zshrc`. If the first `ffmpeg` on PATH is the Intel
  one, set `transcribe.ffmpeg_path = "/opt/homebrew/bin/ffmpeg"`.

## Licenses

raida is MIT. Notable third-party terms: NVIDIA Parakeet TDT 0.6B v3 weights are CC-BY-4.0
(this notice is the attribution); PyMuPDF is AGPL-3.0, acceptable for single-user local use and
swappable via `pdf.extractor` (see `docs/adr/0004-pdf-extraction-behind-interface-agpl-boundary.md`);
Whisper weights are MIT; gpt-oss, Qwen and Gemma 4 weights are Apache-2.0.

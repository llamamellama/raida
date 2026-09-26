# raida

raida is an offline harness for turning a pile of sources into one piece of writing. Drop in
text, Markdown, PDF (including scanned pages), Word documents, subtitle files, audio and video;
raida extracts or transcribes them and takes notes on the long ones as soon as they arrive. Any
file, once ready, can join any session. You type an instruction such as "summarize these into one
article", a local model writes the answer while you watch, and you export it as txt, md, pdf or
docx. Nothing leaves the machine: the LLM, speech-to-text and OCR all run on the Mac.

Target machine: Apple Silicon MacBook Pro with 96-128 GB unified memory (smaller machines work
with smaller models; see `docs/model-setup.md`). The app is a local web app: one Python process
serves the UI at `http://127.0.0.1:8765`.

## How it works

```
 browser UI  <-- SSE events / JSON API -->  raida (FastAPI, asyncio)
   drop files                                  |
   instruction                                 |-- cpu pool:   PDF text, OCR (Apple Vision), docx, subtitles
   streamed answer                             |-- gpu slot:   transcription (Parakeet / Whisper / Apple)
   export buttons                              |-- llm gate:   answers first; notes and read-ahead in the background
                                               |              (llama-server, Ollama or any OpenAI-compatible server)
                                               |-- ffmpeg:     audio extraction from audio/video files
                                               `-- SQLite + files under the data directory
```

Sources are processed concurrently within per-resource limits: several PDFs parse on the CPU
while one file transcribes on the GPU. Every processed file is cached by content hash, so the
same file is never transcribed twice.

Reading a prompt costs a local model time that grows faster than its length: on an M2 Max a 30B
model reads 16k tokens in about 30 s but 100k tokens (four two-hour recordings) in about 14
minutes. So raida does the question-independent work when a file is added. Each long source is
split into sections of about 25 minutes of speech and noted, with time codes or page anchors, at
about a fifth of its length, as background work that pauses whenever an answer is being written.
A two-hour recording is ready about 3.5 minutes after it is added. An answer then reads short
sources in full and long ones through their notes, plus verbatim passages found for the
question. With llama-server, each session's sources are also read into the model's prompt cache
when the session is opened and after every answer, so a question starts answering after reading
only itself (the server keeps about six recent sessions this way; an older one is read again, in
the background, when it is opened). "Read full text" on a question reads every source in full
instead (condensing for the instruction first when they do not fit), for when every word
matters. Details and measurements:
`docs/adr/0005-prepare-sources-at-ingest-answer-from-notes.md`.

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
./scripts/setup-mac.sh        # Homebrew deps (ffmpeg, pango, uv, llama.cpp, ollama), Python env, models
make llm MODEL=gpt-oss:120b   # terminal 1: the model server
make doctor                   # terminal 2: every line should read [OK  ]
make dev                      # terminal 2: starts the app and opens the browser
```

`scripts/setup-mac.sh` copies `raida.example.toml` to `raida.toml`, installs the pinned Python
environment with `uv`, pulls the configured LLM with Ollama (default `gpt-oss:120b`, about 65
GB) and downloads the transcription weights (about 6 GB). The pulls are the slow part.

The model server has to be running whenever raida runs. `make llm MODEL=<ollama tag>` starts
llama.cpp's `llama-server` on the weights Ollama downloaded, read in place, with the settings
raida expects: 3 slots sharing an 80k-token f16 KV cache, 8 GB of RAM for the prompts of idle
sessions, a 1024-token reasoning budget per answer, and unloading after an hour without
requests (`scripts/llama-server.sh` lists the overrides). `llm.model` must equal the tag. To use
Ollama itself instead, set `llm.backend = "ollama"` and `llm.base_url = "http://127.0.0.1:11434"`
and run `make ollama`; answers work the same, but Ollama keeps only the last prompt, so
sessions are not read ahead.

## Using the app

1. Drop files onto the left pane or click "Choose files". Very large local videos can be
   referenced in place with "Add by path" (folders must be listed under `paths.allowed_roots`).
   Every file added in any session is in the shared library: "From library" lists them, and
   adding one to another session reuses its processed text, so it is ready at once. A file
   leaves the library when its last session removes it.
2. Each source shows its stage (extracting, decoding audio, detecting language, transcribing,
   OCR, taking notes) and a progress bar. Failed sources show the reason and a Re-run button.
   "View text" shows the full processed text, with `[p. N]` page anchors or `[hh:mm:ss]` time
   codes; "View notes" shows the notes answers read for a long source.
3. Type an instruction and press Run (or Cmd+Enter). If sources are still processing, the run
   waits for them; tick "Run now with ready sources only" to skip waiting. Tick "Read full text"
   when the answer needs every detail of long sources; it takes minutes for several long
   recordings. The label on each answer says how it was made ("from notes", "full text",
   "condensed then synthesized") and how many of the tokens were already read.
4. The answer streams in as Markdown. Export it as .txt, .md, .pdf or .docx, or copy it.
5. Sessions keep their sources, chat and exports; switch or create sessions from the top bar.
   A new session gets a timestamp name and is renamed after its first answer, in the language
   of the instruction. Rename it yourself at any time; a title you chose is never overwritten.

Instructions can be in any language the model understands, and the answer follows the language
of the instruction, whatever language the sources are in. Chinese gets one more guarantee,
because models drift between its two scripts (Qwen3 answered a Traditional Chinese question
over Traditional Chinese sources in Simplified characters in two runs out of four): the answer
and the session title are held to the script the instruction asks for or is written in, by a
reminder in the instruction's own script and by converting what the model writes with OpenCC as
it streams. The Taiwan or Hong Kong standard is used when the sources' language says so. After
an instruction in another language, Chinese quoted in the answer follows the script of the
sources. Japanese and Korean text is never converted. Notes on Chinese sources are held to the
source's script the same way.

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
| `llm.backend` | `ollama` | `llama_server` (recommended; the example config uses it), `ollama` (native API) or `openai_compatible` (LM Studio, mlx_lm.server) |
| `llm.base_url` | `http://127.0.0.1:11434` | model server (`http://127.0.0.1:8080` for `make llm`) |
| `llm.model` | required | model name as the server knows it; for llama-server its `--alias` |
| `llm.interactive_budget_tokens` | `32000` | largest prompt for a normal answer (notes of long sources, short sources in full) |
| `llm.passage_budget_tokens` | `2000` | verbatim passages retrieved for each question on top of the notes; `0` turns retrieval off |
| `llm.synthesis_budget_tokens` | `64000` | largest prompt for "Read full text"; above it the sources are condensed first. Token estimates are script-aware: Chinese, Japanese and Korean count close to one token per character |
| `notes.enabled` | `true` | take notes on long sources when they are added |
| `notes.min_source_tokens` | `3000` | shorter sources are always read in full |
| `notes.section_tokens` / `notes.ratio` | `4000` / `0.18` | excerpt size behind each set of notes, and notes length relative to it |
| `llm.suggest_titles` | `true` | name a session after its first answer (one short extra model call) |
| `llm.request_timeout_s` | `3600` | how long to wait for the model server. Ollama sends nothing until it has read the whole prompt; very long prompts take many minutes |
| `llm.think` | unset | reasoning before answers. `false` answers without it: text starts at once (measured: under 10 s instead of about 30 s over four recordings' notes) but is less synthesized. With llama-server this works for any model; with Ollama only for hybrid models with a non-thinking mode (Qwen3 2504 tags, Qwen3.5/3.6), since Ollama's thinking-only tags such as Qwen3 Thinking-2507 then leak reasoning into the answer. `"low"`, `"medium"` or `"high"` sets the effort for gpt-oss on Ollama |
| `transcribe.backend` | `parakeet` | `parakeet`, `whisper`, `apple` (macOS 26+, needs `brew install yap`) |
| `transcribe.allow_model_download` | `false` | set `true` only while fetching weights |
| `ocr.languages` | `["en-US"]` | Apple Vision language preference for scanned pages |
| `pdf.extractor` | `pymupdf4llm` | or `pypdfium2` for a permissive-license-only stack |
| `paths.data_dir` | `~/Library/Application Support/raida` | uploads, processed text, exports, SQLite, model cache |
| `paths.allowed_roots` | `[]` | folders allowed for "Add by path" |
| `workers.cpu` / `gpu` / `llm` | `auto` / `1` / `1` | concurrency per resource class |
| `workers.llm_background` | `1` | concurrent background model calls (notes, reading ahead); they pause while an answer is written |
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

- `doctor` says the LLM server is unreachable: start it (`make llm MODEL=<tag>`, or
  `make ollama` with the Ollama backend). "serves 'X' but llm.model is 'Y'": start it with the
  tag in `llm.model`. "raida needs N": start it with a larger context (`RAIDA_LLAMA_CTX`) or
  lower `llm.synthesis_budget_tokens`.
- With Ollama, `doctor` says the model is not pulled: `ollama pull <model>`, then check
  `ollama ps` after a request shows `CONTEXT` at least 74240 and `PROCESSOR 100% GPU`.
- A long source shows "No notes": the model server was not reachable when it was added. It is
  still usable (answers read it in full, which is slower); Re-run it, or restart the app, which
  takes the missing notes in the background.
- Answers from notes miss a detail you know is in a recording: tick "Read full text" for that
  question, or ask for the detail by the words used in the recording, which the passage search
  matches.
- Partial CPU offload or slow first token: the model plus context does not fit the GPU memory
  cap. Raise it with `sudo sysctl iogpu.wired_limit_mb=<MB>` (resets on reboot) or pick a
  smaller model.
- PDF export falls back to fpdf2: install `pango` with Homebrew. `make dev` and `make doctor`
  set `DYLD_FALLBACK_LIBRARY_PATH=/opt/homebrew/lib` so WeasyPrint finds it; export the same
  variable when calling `uv run raida serve` directly.
- Scanned PDF fails with an OCR message: OCR uses Apple Vision and needs `uv sync --extra mac`.
- A source is stuck: cancel it and re-run; the cache means finished work is not repeated.
- The answer ignores the sources, or says they contain nothing relevant, and the "in" token
  count on the answer is tiny: the prompt was larger than the model's context and the server
  dropped the sources. raida now estimates Chinese, Japanese and Korean text at close to one
  token per character (the old rule under-counted almost threefold) and refuses to send a prompt
  that would not fit. If you still hit the limit, raise `llm.synthesis_budget_tokens` for a
  model with a large context, or let map-reduce condense by lowering it.
- A recording fails with "needs a language and language detection is off": the `apple`
  backend cannot detect languages. Pick the language on the source (or under "Language for new
  sources" before adding media); the source re-runs on its own.
- `yap` prints "Downloading required assets" and then `CancellationError`: the Apple engine has
  no recognition assets for that language on this Mac and could not fetch them. Add the language
  under System Settings > Keyboard > Dictation (which downloads them), or pick an installed one.
  For Chinese, pick "Chinese (Traditional)" (`zh-TW`), "Chinese (Simplified)" (`zh-CN`) or
  "Cantonese (Hong Kong)" (`zh-HK`): the transcript is written in the script of the chosen
  locale. Apple's zh-TW engine converts character by character and writes wrong characters
  (外麵 for 外面, 鞦天 for 秋天); raida repairs that with OpenCC. Traditional Chinese
  transcripts made before this fix still carry those characters: Re-run them.
  A `CancellationError` while another transcription is running is transient; raida retries it
  three times on its own.
- Media with no speech (silence, music, a test tone) becomes a ready source with an empty
  transcript, not a failure.
- A folder literally named `~` appeared next to the app: an earlier build did not expand the
  default data directory. Delete that folder; data now lives under
  `~/Library/Application Support/raida` as documented (`raida doctor` prints the path).
- The answer takes a long time to start, or is cut off, with a reasoning model: the model thinks
  first (the line under the answer counts the tokens), raida shows only the final answer, and
  thinking counts against the output limit. llama-server caps it at 1024 tokens
  (`RAIDA_LLAMA_THINK_BUDGET`). With Ollama, raise `llm.output_reserve_tokens` (16384 works for
  Qwen3 Thinking-2507), or set `llm.think = false` for a hybrid model that has a non-thinking
  mode. Reasoning text inside the answer, ending in `</think>`, means `think = false` was set
  for a thinking-only tag: remove it.
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
Whisper weights are MIT; gpt-oss, Qwen and Gemma 4 weights are Apache-2.0; llama.cpp is MIT;
OpenCC is Apache-2.0.

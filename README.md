# Raida

Raida turns a pile of files into one piece of writing, entirely on your Mac. Add documents,
recordings and videos whenever you have them: Raida transcribes them, takes notes on the long
ones, and keeps every file in a library that any session can use. Then ask for what you need (a
summary, meeting minutes, an article, the key points and practical steps) or run a saved skill,
and a local AI model writes the answer while you watch. Export it as .txt, .md, .pdf or .docx.
The model, speech-to-text and OCR all run on the Mac; nothing is uploaded.

## Features

- **Many kinds of sources:** text and Markdown, PDF (scanned pages are read with on-device OCR),
  Word (.docx), subtitles (.srt, .vtt), and common audio and video files (mp3, m4a, wav, mp4, mov,
  mkv and more). Large local videos can be used where they are instead of being uploaded.
- **Processed once, usable everywhere:** a file is transcribed and noted as soon as it is added,
  then any session can use it without waiting. Each session picks its files from the library,
  and files can be renamed.
- **Quick answers over long recordings:** answers read the notes of long sources plus the
  passages that match the question, and each session's sources are read into the model's cache
  before you ask. "Read full text" reads every word when a question needs it.
- **Answers in your language:** the answer follows the language of the instruction. Chinese
  answers keep the script you write in, Traditional or Simplified, with Taiwan or Hong Kong usage
  taken from the sources.
- **Skills:** save an instruction with an example of the output and reference notes, then run it
  in any session with `@name`. Five are built in. Skills use the Agent Skills format that Claude,
  ChatGPT, Codex, Gemini CLI and Cursor also read.
- **Citations and exports:** answers stream in with page anchors and time codes, and export to
  .txt, .md, .pdf and .docx.
- **Command line:** process files, ask questions, run skills and export answers from scripts.

What changed in each version: [CHANGELOG.md](CHANGELOG.md).

## How it works

Raida is a local web app. One Python process serves the page at `http://127.0.0.1:8765`,
processes files in parallel (documents on the CPU, one transcription at a time on the GPU) and
keeps everything under `~/Library/Application Support/raida`. The language model runs in a
separate model server on the same Mac: llama.cpp's `llama-server` (recommended), Ollama, or any
OpenAI-compatible server. Design and decisions: [docs/architecture.md](docs/architecture.md) and
[docs/adr/](docs/adr/).

## Requirements

- A Mac with Apple Silicon (M1 or later) and macOS 14 or newer. The built-in Apple speech engine
  needs macOS 26.
- Memory for the model (step 3): 96 GB or more for the default model, 48 GB for a mid-sized one.
- Free disk: about 85 GB with the default model, about 40 GB with the mid-sized one. That covers
  the model, the speech-to-text weights, the Python environment and the 10 GB Raida keeps free
  for your files.
- Internet access during setup; afterwards Raida works offline. The downloads come from GitHub,
  PyPI, ollama.com and huggingface.co. If your network blocks the model hosts, see "If model
  downloads are blocked" in [docs/user-guide.md](docs/user-guide.md).

## Set up

About 30 minutes plus download time. Type the commands in Terminal, one block at a time. Each
step starts with a check: if it already passes on your Mac, skip the step.

### 1. Install Homebrew

Skip if this prints a version: `/opt/homebrew/bin/brew --version`

```bash
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
```

The installer also installs Apple's Command Line Tools, which include `git` and `make`. Then put
Homebrew on your PATH, as the installer says at the end:

```bash
echo 'eval "$(/opt/homebrew/bin/brew shellenv)"' >> ~/.zprofile
eval "$(/opt/homebrew/bin/brew shellenv)"
```

### 2. Download Raida

```bash
git clone https://github.com/llamamellama/raida.git
cd raida
```

Run every later command in this `raida` folder.

### 3. Choose the model

Skip this step to keep the default, `gpt-oss:120b`, on a Mac with 96 GB or more; the setup
script then creates `raida.toml` for you. Otherwise copy the example settings:

```bash
cp raida.example.toml raida.toml
```

Open `raida.toml` in a plain-text editor (for example `nano raida.toml`; Ctrl+O saves, Ctrl+X
quits) and set `model` in the `[llm]` section:

| Mac memory | `model =` | Download |
| --- | --- | --- |
| 96 GB or more | `"gpt-oss:120b"` (the default) | about 65 GB |
| 48 to 64 GB | `"qwen3:30b-a3b"` | about 19 GB |

Other sizes and models: [docs/model-setup.md](docs/model-setup.md). The measurements in
[docs/benchmarks.md](docs/benchmarks.md) were taken with `qwen3:30b-a3b` on a 96 GB M2 Max.

**Already have a model?** Put its name here: the tag `ollama list` shows, or the name your own
server uses. If your own server runs it, also set `backend` and `base_url` as in step 5.

### 4. Install the rest

```bash
./scripts/setup-mac.sh
```

The script installs ffmpeg, pango (for PDF export), uv, llama.cpp and Ollama with Homebrew,
skipping any already installed, and the Python environment. It then downloads the model from
step 3 with Ollama and the speech-to-text weights (about 6 GB), and ends with `raida doctor`,
whose `llm` line fails until the model server runs (step 5).

- **Your model is already downloaded, or your own server runs it:** add `--skip-llm`.
- **You will use only the Apple speech engine, or Hugging Face is blocked:** add
  `--skip-speech-models`, then set `backend = "apple"` and `language_detection = false` under
  `[transcribe]` in `raida.toml`. That engine needs macOS 26 and
  `/opt/homebrew/bin/brew install yap`, and you choose each recording's language yourself.
- **A download fails with a TLS or "socket is not connected" error:** your network blocks the
  model hosts. See "If model downloads are blocked" in [docs/user-guide.md](docs/user-guide.md).

### 5. Start the model server

Skip if you already run a model server (see the table below).

Open a second Terminal window, `cd` to the `raida` folder, and start the server with the model
name from step 3:

```bash
make llm MODEL=gpt-oss:120b
```

This starts llama.cpp's `llama-server` on the file Ollama downloaded, read in place, with the
settings Raida expects. Loading takes 10 to 60 seconds; leave the window open. From another
window, `curl -s http://127.0.0.1:8080/health` prints `{"status":"ok"}` once it is up. Ollama
itself does not need to keep running.

This works for models Ollama stores as standard GGUF files, and was verified with
`qwen3:30b-a3b`; `gpt-oss:120b` has not been verified with it yet. If llama-server cannot load
your model, let Ollama serve it (the Ollama row below).

**Already run a model server?** Point Raida at it in `raida.toml` and skip `make llm`:

| Your server | `backend =` | `base_url =` | `model =` |
| --- | --- | --- | --- |
| llama.cpp `llama-server` | `"llama_server"` | `"http://127.0.0.1:8080"` | the `--alias` it was started with |
| Ollama | `"ollama"` | `"http://127.0.0.1:11434"` | a tag from `ollama list` |
| LM Studio, `mlx_lm.server` or another OpenAI-compatible server | `"openai_compatible"` | its address, such as `"http://127.0.0.1:1234"` | the model name it lists |

llama-server gives the quickest answers because it keeps several sessions' prompts cached; with
the others, the first question in a session reads its sources first. The setup script leaves
Ollama running; after a restart of the Mac, start it with `make ollama`. `make doctor` (next
step) says whether Raida reaches the server and whether its context is large enough.

### 6. Start Raida

Back in the first window:

```bash
make doctor
```

Each check should read `[OK  ]` and the last line `overall: OK`; a failing line says what to fix.
Then:

```bash
make dev
```

Raida opens in your browser at `http://127.0.0.1:8765`. Ctrl+C stops it. Next time, starting
Raida takes only steps 5 and 6.

## First steps

1. With the Apple speech engine, choose the language under "Language for new sources" before
   adding recordings. The other engines detect it.
2. Drop files onto the Sources tab, or click "Choose files". Each file shows its stage; long
   ones also take notes, which is what makes later questions quick.
3. When the files say Ready, type an instruction, for example "Summarize these into one article
   of about 800 words, with a list of open questions", and press Run (Cmd+Enter).
4. Or type `@` and pick a skill, such as `@key-takeaways` or `@meeting-minutes`.
5. Export the answer with the buttons under it.

A new session starts with no files; add any file from the library in the Sources tab.
[docs/user-guide.md](docs/user-guide.md) covers everything else, including skills, renaming
files, languages and troubleshooting.

## Configuration

Settings live in `raida.toml`; restart `make dev` after changing them. `raida.example.toml`
shows the settings people change, with comments, and `src/raida/config.py` defines every key and
its default. Only `llm.model` is required. Any key can also be set with an environment variable
`RAIDA_<SECTION>__<KEY>`, for example `RAIDA_LLM__MODEL=qwen3:30b-a3b`.

| Key | Default | What it does |
| --- | --- | --- |
| `llm.backend` | `ollama`; the example file sets `llama_server` | `llama_server`, `ollama` or `openai_compatible` |
| `llm.base_url` | `http://127.0.0.1:11434`; the example sets `:8080` | address of the model server |
| `llm.model` | required | the model's name on that server |
| `llm.think` | unset | `false` makes answers start at once, with less structure; [docs/model-setup.md](docs/model-setup.md) says which models allow it |
| `llm.suggest_titles` | `true` | name each session after its first answer |
| `transcribe.backend` | `parakeet` | `parakeet` (25 European languages), `whisper` (99 languages), `apple` (macOS 26, needs `yap`) |
| `transcribe.language_detection` | `true` | detect each recording's language with Whisper; set `false` with the `apple` engine |
| `paths.allowed_roots` | `[]` | folders "Add by path" may read, for example `["~/Movies"]` |
| `paths.data_dir` | `~/Library/Application Support/raida` | uploads, transcripts, notes, exports, skills and the database |
| `server.port` | `8765` | the app's port |

Prompt sizes (`llm.*_budget_tokens`), notes, OCR, PDF extraction and concurrency are described in
`raida.example.toml` and [docs/model-setup.md](docs/model-setup.md). The model server's own
settings (context size, slots, reasoning budget, idle unload) are environment variables of
`make llm`, listed at the top of `scripts/llama-server.sh`. Set them on the same line, for
example `RAIDA_LLAMA_CTX=98304 make llm MODEL=qwen3:30b-a3b`: exported in your shell instead,
Raida reads them as its own settings and refuses to start.

## Command line

```bash
uv run raida doctor                                  # check model, speech-to-text, OCR, PDF export, disk
uv run raida serve [--port N] [--no-open]            # the app (make dev also sets the PDF library path)
uv run raida process a.pdf b.mp3 [--language zh-TW]  # process files and print their text
uv run raida ask <session-id> "instruction"          # or "@skill-name and more text"
uv run raida export <message-id> --format pdf --output out.pdf
uv run raida skills [show <name>]                    # list skills, or print one as SKILL.md
```

`raida --config <file> <command>` or the `RAIDA_CONFIG` variable uses another settings file.

## Troubleshooting setup

| What you see | What to do |
| --- | --- |
| `make doctor`: "LLM server unreachable" | Start the model server (step 5) |
| `make doctor`: "llama-server serves 'X' but llm.model is 'Y'" | Restart `make llm` with the name in `llm.model` |
| `make doctor`: "raida needs N" tokens of context | Start the server with more, `RAIDA_LLAMA_CTX=<N> make llm MODEL=...`, or lower `llm.synthesis_budget_tokens` |
| `make doctor` with the Ollama backend: the model is not pulled | `ollama pull <model>`, or correct `llm.model` |
| Raida refuses to start with "Extra inputs are not permitted" | A `RAIDA_LLAMA_*` variable is exported in your shell. Unset it and give it on the `make llm` line only |
| `uv: command not found`, or `which brew` prints `/usr/local/bin/brew` | The Apple Silicon Homebrew is not first on PATH: run the two lines at the end of step 1, or call `/opt/homebrew/bin/uv`. If the first `ffmpeg` on PATH is an Intel build, set `transcribe.ffmpeg_path = "/opt/homebrew/bin/ffmpeg"` |
| The model is slow and runs partly on the CPU | The model and its context exceed macOS's GPU memory cap. See "GPU memory cap" in [docs/model-setup.md](docs/model-setup.md), or pick a smaller model |
| PDF export looks plain | `/opt/homebrew/bin/brew install pango`, then start Raida with `make dev` |

Problems while using the app: section 8 of [docs/user-guide.md](docs/user-guide.md).

## Documentation

- [docs/user-guide.md](docs/user-guide.md): using Raida step by step, and troubleshooting
- [docs/skills.md](docs/skills.md): making, sharing and running skills
- [docs/model-setup.md](docs/model-setup.md): choosing and tuning models, GPU memory, copying
  models to an offline Mac
- [docs/architecture.md](docs/architecture.md) and [docs/adr/](docs/adr/): how Raida works and why
- [docs/benchmarks.md](docs/benchmarks.md): measured speeds on an M2 Max
- [docs/landscape-2026.md](docs/landscape-2026.md): the research behind the choices
- [CHANGELOG.md](CHANGELOG.md): what changed in each version

## Development

```bash
uv sync --all-groups --extra bundled-ffmpeg   # Linux, or macOS without Homebrew ffmpeg
make lint                                     # ruff check and format check
make fmt                                      # format and apply lint fixes
make test                                     # pytest with the fake model and transcriber
make bench                                    # model speed -> docs/benchmarks.md; MEDIA=<recording> adds transcription
```

Tests run on Linux CI without MLX or a model server: Apple-only imports are lazy, and the `fake`
model and transcriber stand in. `RAIDA_LLM__BACKEND=fake RAIDA_TRANSCRIBE__BACKEND=fake` runs the
whole app without any model. Logs are JSON lines on stdout. The app serves the UI files it
started with, so restart `make dev` after editing `src/raida/web`. Conventions for contributors
and coding agents are in [AGENTS.md](AGENTS.md); every user-visible change updates this README and
[CHANGELOG.md](CHANGELOG.md).

Layout: `src/raida/api` (routes, SSE), `src/raida/pipeline` (scheduler, stages),
`src/raida/transcribe`, `src/raida/llm`, `src/raida/skills`, `src/raida/export`,
`src/raida/web` (static UI), `tests/`, `scripts/`, `docs/`.

## Licenses

Raida is MIT-licensed. Third-party terms: NVIDIA Parakeet TDT 0.6B v3 weights are CC-BY-4.0
(this notice is the attribution); PyMuPDF is AGPL-3.0, acceptable for single-user local use and
swappable with `pdf.extractor` (see
[docs/adr/0004-pdf-extraction-behind-interface-agpl-boundary.md](docs/adr/0004-pdf-extraction-behind-interface-agpl-boundary.md));
Whisper weights are MIT; gpt-oss, Qwen and Gemma 4 weights are Apache-2.0; llama.cpp is MIT;
OpenCC is Apache-2.0.

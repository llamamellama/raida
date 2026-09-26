# raida user guide

raida turns a pile of files into one piece of writing, entirely on your Mac. Drop in documents,
recordings and videos, type what you want (a summary, an article, meeting minutes, a comparison),
and a local AI model writes the answer while you watch. Nothing is uploaded anywhere.

This guide is for people who want to use raida. Developers should read `../README.md` and
`architecture.md` as well.

## 1. What you need

- An Apple Silicon Mac (M1 or later). 96 GB of memory or more runs the largest models; 32 GB is
  enough for a mid-sized model.
- macOS 15 or newer. macOS 26 adds the built-in Apple transcription engine.
- About 25 GB of free disk for a mid-sized model, 70 GB for the largest.
- Network access once, to install packages and download the model. Afterwards raida works
  offline. If your network blocks model downloads, see section 7.

## 2. Install (once, about 20 minutes plus download time)

Open Terminal and run these commands one at a time. Each one prints what it is doing.

```bash
git clone <repository url> raida
```

```bash
cd raida && ./scripts/setup-mac.sh
```

The setup script installs Homebrew packages (ffmpeg, pango, uv, ollama), the Python environment,
creates your personal configuration file `raida.toml`, starts Ollama and downloads the models.
The download is the slow part.

When it finishes, check the installation:

```bash
make doctor
```

Every line should read `[OK  ]`. If one does not, the message says what to install or fix.

## 3. Start the app

raida needs two things running: the model server (Ollama) and raida itself. Use two Terminal
windows in the `raida` folder.

Window 1, the model server:

```bash
make ollama
```

Window 2, the app:

```bash
make dev
```

Your browser opens at `http://127.0.0.1:8765`. The status strip under the title shows the model,
the transcription engine, OCR and PDF export. A green item is ready; a red one tells you why not.

The first request after starting loads the model into memory, which can take 10 to 60 seconds.
After that it stays loaded for an hour of inactivity.

To stop: press Ctrl+C in both windows.

## 4. Use the app

### Add sources

Drag files onto the left pane, or click "Choose files". Supported: text, Markdown, PDF
(including scanned pages, which are recognized with on-device OCR), Word documents, subtitle
files (.srt, .vtt), and any audio or video ffmpeg can read.

Each source shows its stage (extracting, decoding audio, transcribing, OCR) and a progress bar.
Several files process at once. A file you have added before, in any session, is ready instantly
because raida remembers processed content by file hash.

For very large videos, use "Add by path" instead of uploading: the file stays where it is. The
folder must be listed under `paths.allowed_roots` in `raida.toml` (the default list contains
`~/Movies` and `~/Downloads`).

Click "View text" on a source to see exactly what the model will read, with `[p. N]` page anchors
for documents and `[hh:mm:ss]` time codes for recordings.

### Set the language for recordings

Sources default to automatic language detection. With the Apple transcription engine there is no
detector, so choose the language under "Language for new sources" before adding audio or video.
You can also change the language on a single source afterwards; it re-transcribes. Chinese comes
in three choices: Traditional (Taiwan), Simplified, and Cantonese (Hong Kong); the transcript
uses the script of the one you pick.

Recordings with no speech (music, silence) become a ready source with an empty transcript.

### Ask for the writing

Type an instruction in the box at the bottom right and press Run (or Cmd+Enter). Good
instructions say what to produce, for whom, and how long:

- "Summarize these three sources into one article of about 800 words, with a short intro and a
  list of open questions."
- "Write meeting minutes from the recording: decisions, owners, deadlines. Keep the time codes."
- "Compare what the two reports say about cost, in a table, then give a one-paragraph verdict."

If sources are still processing, the run waits for them. Tick "Run now with ready sources only"
to skip the waiting ones.

The answer streams in as formatted text with citations back to the sources, such as
`[notes.md]` or `[p. 3]`. With a reasoning model, the first words can take a while to appear:
the model thinks first and raida shows only the final answer. Long inputs take longer to read;
inputs larger than the model's context budget are condensed source by source first, which shows
as a "Condensing" stage.

You can keep the conversation going: follow-up instructions see the earlier answers.

### Export

Under each answer: export as .txt, .md, .pdf or .docx, or copy it. Exports are saved under the
data folder and downloaded by the browser.

### Sessions

A session holds its sources, conversation and exports. Use the top bar to create, rename, switch
or delete sessions. Deleting a session removes its uploaded copies; files added by path are never
deleted.

## 5. Where your data lives

Everything is in `~/Library/Application Support/raida`: uploads, extracted text, transcripts,
exports and a small database. Deleting that folder resets raida. `raida doctor` prints the path.

## 6. Changing the model or settings

Settings are in `raida.toml` in the `raida` folder. The example file `raida.example.toml` lists
every key with its default. The ones people change:

| Key | What it does |
| --- | --- |
| `llm.model` | The model Ollama should use, as shown by `ollama list` |
| `llm.think` | `false` turns off reasoning for models that support it, for faster answers; leave unset for thinking-only models |
| `transcribe.backend` | `parakeet` (default, best accuracy), `whisper` (all languages), `apple` (fastest, macOS 26+, needs an explicit language) |
| `paths.allowed_roots` | Folders allowed for "Add by path" |
| `server.port` | Change if 8765 is taken |

Restart `make dev` after editing. `model-setup.md` explains which model fits which Mac.

To switch models: `ollama pull <name>`, set `llm.model`, restart, and check the status strip
says "100% GPU". Partial GPU means the model is too large for the machine; pick a smaller one.

## 7. If model downloads are blocked

Managed corporate networks often block `ollama.com` and `huggingface.co` while allowing package
installs. Symptoms: `ollama pull` fails with a TLS or "socket is not connected" error while the
rest of the setup succeeds.

Options, in order of preference:

1. Ask IT for an exception for the hosts listed under Prerequisites in `../README.md`.
2. Pull the models on another network and copy `~/.ollama/models` (and the transcription
   weights under the data folder's `models/hf`) to the work Mac. Ollama serves them offline.
3. Meanwhile, use what is already there: set `llm.model` to a model `ollama list` shows,
   `transcribe.backend = "apple"` (macOS 26+, `brew install yap`) and
   `transcribe.language_detection = false`. Everything works; you pick the language for
   recordings yourself.

## 8. Troubleshooting

| What you see | What to do |
| --- | --- |
| Status strip says the model is not pulled | Run `ollama pull <model>` with the name from `raida.toml` |
| Status strip says the model is not loaded | Normal before the first request. Make sure `make ollama` is running |
| The browser cannot connect | `make dev` is not running, or the port is taken; check Window 2 |
| A recording fails with "needs a language and language detection is off" | Pick the language on the source card, or under "Language for new sources" before adding media. The source re-runs on its own |
| A recording fails with "Downloading required assets" and `CancellationError` | This Mac has no Apple recognition assets for that language and could not fetch them. Add it under System Settings > Keyboard > Dictation, or pick an installed language. raida already retries the transient form of this error |
| Chinese transcript comes out in the wrong script | Pick "Chinese (Traditional)" for Traditional characters, "Chinese (Simplified)" for Simplified, or "Cantonese (Hong Kong)". Changing the language on a source re-transcribes it |
| The answer starts after a long pause | The model is reasoning first. Wait, or set `llm.think = false` for a model that supports it |
| The answer is cut off | Raise `llm.output_reserve_tokens` in `raida.toml` (16384 is generous) |
| Slow, and the strip shows less than 100% GPU | The model does not fit in GPU memory. Use a smaller model or raise the cap as described in `model-setup.md` |
| PDF export looks plain | Install `pango` with Homebrew (`/opt/homebrew/bin/brew install pango`) and start with `make dev` |
| A scanned PDF fails with an OCR message | Run `uv sync --extra mac` in the `raida` folder |

## 9. Command line, for scripts

```bash
uv run raida doctor                          # check everything
uv run raida serve --port 9000 --no-open     # start without opening the browser
uv run raida process report.pdf talk.mp3     # extract and transcribe, print the text
uv run raida ask <session-id> "Summarize."   # run an instruction against a session
uv run raida export <message-id> --format docx --output answer.docx
```

`process` prints the session id at the end; `ask` prints the message id. Both need Ollama
running for `ask`.

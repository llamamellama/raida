# raida user guide

raida turns a pile of files into one piece of writing, entirely on your Mac. Drop in documents,
recordings and videos whenever you have them: raida transcribes them and takes notes on them
right away, and keeps them in a library any session can use. Then type what you want (a
summary, an article, meeting minutes, a comparison), and a local AI model writes the answer
while you watch. Nothing is uploaded anywhere.

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

The setup script installs Homebrew packages (ffmpeg, pango, uv, llama.cpp, ollama), the Python
environment, creates your personal configuration file `raida.toml`, and downloads the models
with Ollama. The download is the slow part.

When it finishes, check the installation:

```bash
make doctor
```

Every line should read `[OK  ]`. If one does not, the message says what to install or fix.

## 3. Start the app

raida needs two things running: the model server (llama-server, from llama.cpp) and raida
itself. Use two Terminal windows in the `raida` folder.

Window 1, the model server, with the model name from `llm.model` in `raida.toml`:

```bash
make llm MODEL=gpt-oss:120b
```

It reads the weights Ollama downloaded; nothing is copied. If your `raida.toml` says
`backend = "ollama"`, run `make ollama` here instead.

Window 2, the app:

```bash
make dev
```

Your browser opens at `http://127.0.0.1:8765`. The status strip under the title shows the model,
the transcription engine, OCR and PDF export. A green item is ready; a red one tells you why not.

The model server loads the model into memory when it starts, which can take 10 to 60 seconds.
After an hour without requests it unloads it to give the memory back, and loads it again on the
next request.

To stop: press Ctrl+C in both windows.

## 4. Use the app

### Add sources

Drag files onto the left pane, or click "Choose files". Supported: text, Markdown, PDF
(including scanned pages, which are recognized with on-device OCR), Word documents, subtitle
files (.srt, .vtt), and any audio or video ffmpeg can read.

Each source shows its stage (extracting, decoding audio, transcribing, OCR, taking notes) and a
progress bar. Several files process at once. A file you have added before, in any session, is
ready instantly because raida remembers processed content and notes by file hash.

Long sources (more than a few pages, or more than a few minutes of speech) get notes as soon
as their text is ready: raida splits them into sections of about 25 minutes of speech and writes
anchored notes of about a fifth of their length, then a short overview. This is what lets later
questions over several long files answer quickly. On an M2 Max a two-hour recording is ready
about 3.5 minutes after you add it: one minute to transcribe, the rest for notes. The notes are
taken in the background and pause whenever an answer is being written, so you can keep working.
Click "View notes" on a source to read them.

For very large videos, use "Add by path" instead of uploading: the file stays where it is. The
folder must be listed under `paths.allowed_roots` in `raida.toml` (the default list contains
`~/Movies` and `~/Downloads`).

Click "View text" on a source to see exactly what the model will read, with `[p. N]` page anchors
for documents and `[hh:mm:ss]` time codes for recordings.

Every file you have ever added is in the shared library. Click "From library" in a new session,
tick the files you want, and they appear ready within a second because their processed text is
reused. A file leaves the library when the last session that holds it removes it.

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

A normal answer reads short sources in full and long ones through their notes, plus the passages
of the full text that match your question. That is fast: when you open a session, raida reads
its sources into the model's memory in advance (about a minute for four two-hour recordings,
while you type), so the model starts on your question right away. It then thinks for up to about
25 seconds before writing; set `llm.think = false` in `raida.toml` if you prefer the text to
start at once, with somewhat less structure. If you ask before the advance reading has finished,
or in a session with a new combination of files, the first answer waits for the reading (about a
minute for three two-hour recordings). Tick "Read full text" when you need every detail of long
sources, for example exact quotes from the whole of a recording. It reads everything word for
word and takes minutes for several long recordings. The label on each answer says which way it
was made ("from notes", "full text", "condensed then synthesized"), and the token count shows
how much was already read in advance.

Write the instruction in whatever language you like. The answer comes back in the language of
your instruction, whatever language the sources are in: an instruction in English gets an
English answer about Mandarin recordings, an instruction in Traditional Chinese a Traditional
Chinese answer about English recordings. For Chinese, raida makes sure of the script: an
instruction written in Traditional characters always gets Traditional characters (Taiwan usage
when the recordings were set to "Chinese (Traditional)"), even when the model slips into
Simplified, and the same for Simplified. To get the other script, say so in the instruction
("請用簡體中文", "in Simplified Chinese").

The answer streams in as formatted text with citations back to the sources, such as
`[notes.md]`, `[p. 3]` or `[00:14:32]`. With a reasoning model, the first words can take a
while to appear: the model thinks first (the line under the answer counts its thinking tokens)
and raida shows only the final answer. With "Read full text", inputs larger than the model's
context budget are condensed source by source first, which shows as a "Condensing" stage.

You can keep the conversation going: follow-up instructions see the earlier answers.

### Export

Under each answer: export as .txt, .md, .pdf or .docx, or copy it. Exports are saved under the
data folder and downloaded by the browser.

### Sessions

A session holds its sources, conversation and exports. Use the top bar to create, rename, switch
or delete sessions. A new session is named with the date and time, then renamed after its first
answer to match what you asked, in your language. Rename it yourself whenever you like; a name
you chose is never changed automatically. Deleting a session removes its uploaded copies unless
another session still uses them; files added by path are never deleted.

## 5. Where your data lives

Everything is in `~/Library/Application Support/raida`: uploads, extracted text, transcripts,
exports and a small database. Deleting that folder resets raida. `raida doctor` prints the path.

## 6. Changing the model or settings

Settings are in `raida.toml` in the `raida` folder. The example file `raida.example.toml` lists
every key with its default. The ones people change:

| Key | What it does |
| --- | --- |
| `llm.model` | The model to use, as shown by `ollama list`; pass the same name to `make llm MODEL=...` |
| `llm.think` | `false` makes answers start at once instead of after about half a minute of thinking, at some cost in structure. With the Ollama backend, only for models that support it; leave unset for thinking-only models there |
| `transcribe.backend` | `parakeet` (default, best accuracy), `whisper` (all languages), `apple` (fastest, macOS 26+, needs an explicit language) |
| `paths.allowed_roots` | Folders allowed for "Add by path" |
| `server.port` | Change if 8765 is taken |

Restart `make dev` after editing. `model-setup.md` explains which model fits which Mac.

To switch models: `ollama pull <name>`, set `llm.model`, restart both windows with the new
name (`make llm MODEL=<name>`). Notes are taken again for every long source with the new
model, in the background.

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
| Status strip says the LLM server is unreachable | Start it in Window 1: `make llm MODEL=<model>` (or `make ollama` with the Ollama backend) |
| Status strip says the server serves another model | Restart Window 1 with the name in `llm.model` |
| Status strip says the model is not pulled (Ollama backend) | Run `ollama pull <model>` with the name from `raida.toml` |
| A long source says "No notes" | The model server was not running when it was added. It still works, more slowly. Click Re-run, or restart the app: missing notes are taken in the background |
| An answer from notes misses a detail | Ask again with "Read full text" ticked, or use the words spoken in the recording, which the passage search matches |
| The browser cannot connect | `make dev` is not running, or the port is taken; check Window 2 |
| A recording fails with "needs a language and language detection is off" | Pick the language on the source card, or under "Language for new sources" before adding media. The source re-runs on its own |
| A recording fails with "Downloading required assets" and `CancellationError` | This Mac has no Apple recognition assets for that language and could not fetch them. Add it under System Settings > Keyboard > Dictation, or pick an installed language. raida already retries the transient form of this error |
| Chinese transcript comes out in the wrong script | Pick "Chinese (Traditional)" for Traditional characters, "Chinese (Simplified)" for Simplified, or "Cantonese (Hong Kong)". Changing the language on a source re-transcribes it |
| A Traditional Chinese transcript has wrong characters such as 外麵 or 鞦天 | It was made before raida corrected the Apple engine's character conversion. Click Re-run on the source |
| An older answer is in Simplified Chinese although you asked in Traditional | It was written before raida held answers to the instruction's script. Ask again |
| The answer starts after a long pause | The model is reasoning first. Wait, or set `llm.think = false` for a model that supports it |
| The answer is cut off | Raise `llm.output_reserve_tokens` in `raida.toml` (16384 is generous) |
| The run fails with "did not answer within llm.request_timeout_s" | The model was still reading a very long prompt when raida stopped waiting. Raise `llm.request_timeout_s` in `raida.toml`, or use fewer or shorter sources per question. The status under the answer shows how many tokens are being read |
| The answer claims the sources say nothing relevant, and its "in" count is tiny | The prompt did not fit the model's context. raida now measures Chinese, Japanese and Korean text correctly and refuses to send a prompt that would overflow; if you see the refusal, raise `llm.synthesis_budget_tokens` for a large-context model or lower it to let raida condense first |
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

`process` prints the session id at the end; `ask` prints the message id. `ask`, and notes on
long files in `process`, need the model server running.

# raida user guide

raida turns a pile of files into one piece of writing, entirely on your Mac. Drop in documents,
recordings and videos whenever you have them: raida transcribes them and takes notes on them
right away, and keeps them in a library any session can use. Then type what you want (a
summary, an article, meeting minutes, a comparison), and a local AI model writes the answer
while you watch. Nothing is uploaded anywhere.

This guide is for people who want to use raida. Developers should read `../README.md` and
`architecture.md` as well.

## 1. What you need

An Apple Silicon Mac (M1 or later) with macOS 14 or newer, enough memory for the model (48 GB for
a mid-sized one, 96 GB or more for the default), and 40 to 85 GB of free disk. "Requirements" in
`../README.md` has the details. Setup needs network access once; afterwards raida works offline.
If your network blocks model downloads, see section 7.

## 2. Install (once, about 30 minutes plus download time)

Follow "Set up" in `../README.md`. It starts from a Mac with nothing installed (Homebrew, then
raida, the model and the model server), and each step says how to tell whether you can skip it,
for example when you already have a model in Ollama or run your own model server.

## 3. Start the app

raida needs two things running: the model server (llama-server, from llama.cpp) and raida
itself. Use two Terminal windows in the `raida` folder.

Window 1, the model server, with the model name from `llm.model` in `raida.toml`:

```bash
make llm MODEL=gpt-oss:120b
```

It reads the weights Ollama downloaded; nothing is copied. If your `raida.toml` says
`backend = "ollama"` (with `base_url = "http://127.0.0.1:11434"`), Ollama serves the model
instead: the setup script leaves it running, and after a restart of the Mac you start it here
with `make ollama`. With a model server of your own, start that one.

Window 2, the app:

```bash
make dev
```

Your browser opens at `http://127.0.0.1:8765`. The status strip at the top right shows the model,
the transcription engine, OCR and PDF export. A green item is ready; a red one tells you why not.
Hover over an item for the full detail.

The model server loads the model into memory when it starts, which can take 10 to 60 seconds.
After an hour without requests it unloads it to give the memory back, and loads it again on the
next request.

To stop: press Ctrl+C in both windows.

## 4. Use the app

### Add sources

Drag files onto the Sources tab on the left (or anywhere on the page), or click "Choose files".
Supported: text and Markdown, PDF (including scanned pages, which are recognized with on-device
OCR), Word (.docx), subtitle files (.srt, .vtt), and common audio and video files (mp3, m4a,
wav, aac, flac, ogg, mp4, m4v, mov, mkv, webm, avi and more).

Each source shows its stage (extracting, decoding audio, transcribing, OCR, taking notes) and a
progress bar. Several files process at once. A file you have added before, in any session, is
not processed again: raida recognizes it by its content and uses the copy in the library.

Long sources (more than a few pages, or more than a few minutes of speech) get notes as soon
as their text is ready: raida splits them into sections of about 25 minutes of speech and writes
anchored notes of about a fifth of their length, then a short overview. This is what lets later
questions over several long files answer quickly. On an M2 Max a two-hour recording is ready
about 3.5 minutes after you add it: one minute to transcribe, the rest for notes. The notes are
taken in the background and pause whenever an answer is being written, so you can keep working.
Click "View notes" on a source to read them.

For very large videos, use "Add by path" instead of uploading: the file stays where it is. The
folder must be listed under `paths.allowed_roots` in `raida.toml`, for example
`allowed_roots = ["~/Movies", "~/Downloads"]`; the list is empty until you add folders.

Click "View text" on a source to see exactly what the model will read, with `[p. N]` page anchors
for documents and `[hh:mm:ss]` time codes for recordings.

### Use files from the library

Every file you add, in any session, goes into the library: it is transcribed and noted once and
stays available to every session. The Sources tab has two parts:

- **In this session:** the files this session's answers read. A new session starts with none.
- **Library:** every other file, most recently used first, with its length and how many
  sessions use it. Click "Add to session" to use one here. It is ready at once, because nothing
  is processed again; a recording still being transcribed for another session is shared, and a
  question asked meanwhile waits for it. With many files, a filter box appears above the list.

"Remove" on a file in this session takes it out of the session only; it stays in the library.
"Delete" in the library list deletes the file everywhere: it leaves every session that uses it,
and its transcript, notes and uploaded copy are deleted (a file added by path stays where it
is). Deleting a session never deletes its files.

A file's language belongs to the file, not to a session: changing it on a recording
re-transcribes it for every session that uses it.

### Rename a file

Long downloaded names such as `讀書會2026.9.17第248堂「愛，無所不在」（上） [YxtSmUV_04E].mp4` can be
replaced with your own. Click the name of a file (in this session or in the library),
type the new one and press Enter; Escape keeps the old name. The new name shows in every
session, and it is the name the model sees, so answers cite the file by it. Nothing is
transcribed again. Hover over a renamed file to see its original file name; to go back to it,
clear the name and press Enter.

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
while you type), so the model starts on your question right away. The model server keeps about
six recent sessions this way; an older one is read again, in the background, when you open it.
Reading ahead needs the llama-server model server; with Ollama or another server, the first
question in a session reads its sources first. It then thinks for up to about
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

### Skills: save an instruction and reuse it

When you ask for the same kind of writing again and again, save it as a skill. A skill holds
the instructions, an example of the format the answer should take, and reference notes such as
the correct spelling of names a recording gets wrong. Skills belong to the app: the **Skills**
tab in the sidebar lists them all, and every session can run any of them.

- **Run one:** type `@` at the start of the instruction box. A menu lists the skills; keep
  typing to filter, then press Enter (or click one). Add words after the command to adjust this
  run, for example `@key-takeaways 著重在家庭關係`, and press Run. Or click **Use** on a skill
  in the Skills tab. The question shows a "skill" badge, and "Instruction sent to the model"
  shows the whole instruction.
- **Built in:** `@summary`, `@key-takeaways` (key points, then practical steps),
  `@meeting-minutes`, `@article` and `@questions-answers`. They answer in the language of your
  sources, so Traditional Chinese recordings get Traditional Chinese answers.
- **Make your own:** open the **Skills** tab, then **New skill** (or **Duplicate** one).
  Give it a title in any language, a short command such as `study-notes`, a one-line
  description, and the instructions. The output example is optional: write it the way the
  answer should look, with headings in angle brackets such as `## <Key points>` if the answer
  may be in another language. Choose the answer language (same as the instructions, same as the
  sources, or a fixed one). "Think before answering" gives better structure; switch it off for
  quick jobs, and the answer starts at once.
- **Change or remove a built-in one:** Edit it and save; the list shows "built-in, changed",
  and Reset brings the original back. Delete takes any skill out of the list and the `@` menu,
  built-in ones included; Restore, at the bottom of the Skills tab, brings deleted built-in
  skills back.
- **Share:** Export downloads a .zip; Import takes a .zip, .skill or SKILL.md, including skills
  made for Claude, ChatGPT or Gemini. raida uses only their text and never runs anything in
  them.

More detail, including the file format: `skills.md`.

### Export

Under each answer: export as .txt, .md, .pdf or .docx, or copy it. Exports are saved under the
data folder and downloaded by the browser.

### Sessions

A session holds its conversation and exports, and uses the library files you added to it. Use
the top bar to create, rename, switch or delete sessions. New opens an empty session straight
away, with nothing to confirm: it is named with the date and time, then renamed after its first
answer to match what you asked, in your language. Rename it yourself whenever you like; a name
you chose is never changed automatically. Deleting a session deletes its conversation and
exports; its files stay in the library for other sessions. To free disk space, delete files
from the library.

## 5. Where your data lives

Everything is in `~/Library/Application Support/raida`: uploads, extracted text, transcripts,
notes, exports, your skills (the `skills` folder, one folder per skill), the downloaded
speech-to-text models (`models`) and a small database. `raida doctor` and Settings show the
path.

To start over, open **Settings** (the last button in the top bar) and choose **Nuke** in its
Danger zone. After you type NUKE to confirm, it stops all work and deletes every session, every
library file with its transcript and notes, all exports, your skills and changes to the built-in
ones, and the database backups: Raida is back to factory settings. It keeps `raida.toml`, the
downloaded speech-to-text models, the model server and its models, and the files you added by
path. It cannot be undone. Use it rather than deleting the folder yourself, which would also
delete the models.

## 6. Changing the model or settings

Settings are in `raida.toml` in the `raida` folder. The example file `raida.example.toml` shows
the settings people change, with comments. The most common:

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
installs. Symptom: the setup script stops at the download step with a TLS or "socket is not
connected" error. Everything before that step (Homebrew packages, the Python environment,
`raida.toml`) is installed.

Options, in order of preference:

1. Ask IT for an exception for `ollama.com`, `registry.ollama.ai`, `huggingface.co`,
   `cdn-lfs.hf.co` and `*.xethub.hf.co`.
2. Pull the models on another network and copy `~/.ollama/models` (and the transcription
   weights under the data folder's `models/hf`) to the work Mac. They are then used offline.
3. Meanwhile, use what is already there: set `llm.model` to a model `ollama list` shows,
   `transcribe.backend = "apple"` (macOS 26+, `/opt/homebrew/bin/brew install yap`) and
   `transcribe.language_detection = false`, then finish setup without downloads:
   `./scripts/setup-mac.sh --skip-llm --skip-speech-models`. Everything works; you pick the
   language for recordings yourself.

## 8. Troubleshooting

| What you see | What to do |
| --- | --- |
| Status strip says the LLM server is unreachable | Start it in Window 1: `make llm MODEL=<model>` (or `make ollama` with the Ollama backend) |
| Status strip says the server serves another model | Restart Window 1 with the name in `llm.model` |
| Status strip says the model is not pulled (Ollama backend) | Run `ollama pull <model>` with the name from `raida.toml` |
| A long source says "No notes" | The model server was not running when it was added. It still works, more slowly. Click Re-run, or restart the app: missing notes are taken in the background |
| An answer from notes misses a detail | Ask again with "Read full text" ticked, or use the words spoken in the recording, which the passage search matches |
| The browser cannot connect | `make dev` is not running, or the port is taken; check Window 2 |
| The browser shows "raida only answers requests addressed to this Mac" | Open `http://127.0.0.1:8765` or `http://localhost:8765`. Raida has no login, so it refuses any other address, such as the Mac's network name |
| A recording fails with "needs a language and language detection is off" | Pick the language on the source card, or under "Language for new sources" before adding media. The source re-runs on its own |
| A recording fails with "Downloading required assets" and `CancellationError` | This Mac has no Apple recognition assets for that language and could not fetch them. Add it under System Settings > Keyboard > Dictation, or pick an installed language. raida already retries the transient form of this error |
| Chinese transcript comes out in the wrong script | Pick "Chinese (Traditional)" for Traditional characters, "Chinese (Simplified)" for Simplified, or "Cantonese (Hong Kong)". Changing the language on a source re-transcribes it |
| The answer starts after a long pause | The model is reasoning first. Wait, or set `llm.think = false` for a model that supports it |
| The answer is cut off | Raise `llm.output_reserve_tokens` in `raida.toml` (16384 is generous). With llama-server, give it the larger context too, for example `RAIDA_LLAMA_CTX=90112 make llm MODEL=<model>`; `make doctor` says how much it needs |
| The run fails with "did not answer within llm.request_timeout_s" | The model was still reading a very long prompt when raida stopped waiting. Raise `llm.request_timeout_s` in `raida.toml`, or use fewer or shorter sources per question. The status under the answer shows how many tokens are being read |
| The run fails with "The prompt is about N tokens but the context window leaves room for M" | The sources do not fit the model's context. Remove sources from the session. With a model that has a larger context, raise `llm.synthesis_budget_tokens` and start the model server with that much context |
| A source is stuck in one stage | Click Cancel, then Re-run. Finished work is cached, so nothing done is repeated |
| Answers are slow, and the model runs partly on the CPU (with Ollama, the status strip shows less than 100% GPU) | The model does not fit in GPU memory. Use a smaller model or raise the cap as described in `model-setup.md` |
| PDF export looks plain | Install `pango` with Homebrew (`/opt/homebrew/bin/brew install pango`) and start with `make dev` |
| A scanned PDF fails with an OCR message | Run `uv sync --extra mac` in the `raida` folder |
| "There is no skill named @x" | The text starts with `@` and a word that is not a skill. Pick one from the menu, or start with `@@` to send a text that begins with `@` |
| A skill is missing from the `@` menu, and the Skills tab shows "Could not read ..." | Its `SKILL.md` under `~/Library/Application Support/raida/skills/` cannot be parsed, for example it has no frontmatter or a `name` with capital letters. The message says what to fix |
| The page acts like an older version after an update | Reload it once. An open page normally reloads itself when the app restarts with a new version |
| A skill answers in the wrong language | Edit it and set "Answer language": same as the sources, or a fixed language |

## 9. Command line, for scripts

```bash
uv run raida doctor                          # check everything
uv run raida serve --port 9000 --no-open     # start without opening the browser
uv run raida process report.pdf talk.mp3     # extract and transcribe, print the text
uv run raida ask <session-id> "Summarize."   # run an instruction against a session
uv run raida ask <session-id> "@summary"     # or a skill
uv run raida skills                          # list the skills
uv run raida export <message-id> --format docx --output answer.docx
```

`process` prints the session id at the end; `ask` prints the message id. `ask`, and notes on
long files in `process`, need the model server running.

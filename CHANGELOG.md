# Changelog

All notable changes to raida are recorded here, newest first. Each entry says what changed for
someone using the app; the reasons and measurements are in `docs/adr/`.

The format is based on [Keep a Changelog 1.1.0](https://keepachangelog.com/en/1.1.0/), and
raida uses [Semantic Versioning 2.0.0](https://semver.org/spec/v2.0.0.html). Before 1.0.0, a
minor version can change behavior; upgrade notes say when you need to do something.

## [Unreleased]

### Added

- Skills: save an instruction once and run it in any session by typing `@name` at the start of
  the instruction box, optionally followed by words for this run (`@summary focus on the
  budget`). A skill holds instructions, an optional output example, reference notes (such as
  the correct spelling of names), an answer language and two options: read the full text, and
  think before answering. Five are built in: `@summary`, `@key-takeaways`, `@meeting-minutes`,
  `@article` and `@questions-answers`. Each question records exactly what ran, so editing a
  skill later does not change past answers.
- A Skills tab in the sidebar lists every skill and creates, edits, duplicates, imports and
  exports them. An edited built-in skill can be reset to the original.
- Built-in skills can be deleted like your own, with any changes to them. Restore, at the
  bottom of the Skills tab, brings them back.
- Nuke, in the new Settings dialog (last button in the top bar), returns Raida to factory
  settings. It stops all work and deletes every session, library file, answer, export, skill
  and database backup. It keeps `raida.toml`, the downloaded speech-to-text models and files
  added by path. It sits in a Danger zone and asks you to type NUKE before it runs. Every open
  tab reloads afterwards. Settings also shows the version, the model and the data folder.
- Skills use the Agent Skills format (a folder with `SKILL.md`), so skills made for Claude,
  ChatGPT, Codex, Gemini CLI or Cursor import (`.zip`, `.skill` or `SKILL.md`) and raida's
  exports load there. raida uses only their text and never runs code from a skill.
- One source library for all sessions. Every file added in any session is processed once and
  stays available: the Sources tab shows the files this session uses, then the rest of the
  library, with size, how many sessions use each file and a filter box. "Add to session" makes a
  file ready at once; a file still being processed for another session is shared, not processed
  twice.
- Rename a source by clicking its name. The new name shows in every session and is the name the
  model sees, so answers cite it; clearing it brings back the file name.
- `raida skills` lists skills and `raida skills show <name>` prints one; `raida ask` accepts
  `@name`.
- An open browser tab reloads itself when the app has been updated, keeping unsent text.
- `scripts/setup-mac.sh --skip-llm` finishes setup without downloading the model (it is already
  downloaded, or your own model server runs it), and `--skip-speech-models` without the Parakeet
  and Whisper weights (Apple speech only, or Hugging Face is blocked).
- The README walks through setup from a Mac with nothing installed, including the model server,
  and says at each step how to tell whether you can skip it.

### Changed

- New creates a session at once, with no title to confirm. It is named with the date and time
  and renamed after its first answer.
- A new session starts with no sources. "Remove" takes a file out of the session and keeps it in
  the library; "Delete" in the library list deletes it everywhere, with its transcript, notes
  and uploaded copy. Deleting a session keeps its files.
- A file's language belongs to the file: changing it re-processes the file for every session
  that uses it. Earlier results stay cached, so switching back is immediate.
- The sidebar has Sources and Skills tabs, the top-left corner reads "Raida", the status chips
  are compact, and the layout works at laptop and phone widths.
- Every earlier question and answer in a session goes to the model with each new instruction,
  word for word, so follow-ups such as "make it shorter" work. A question whose answer failed or
  was stopped is left out. The sources keep their share of the prompt
  (`llm.interactive_budget_tokens`, now the sources' share only). The conversation comes on top,
  up to `llm.synthesis_budget_tokens`; past that, the sources least related to the question are
  shortened to their overviews, and the conversation is never cut. A session that outgrows the
  model says so and asks you to continue in a new one.
- A question asked while its session's sources are being read into the model's cache waits for
  that read instead of cancelling it, which could re-read the whole prompt (134 s measured).
- The app serves the UI files it started with; restart it to pick up edits to `src/raida/web`.
- After checking the model, `scripts/pull-models.sh` unloads it from Ollama, so `make llm` does
  not load a second copy while Ollama still holds one.
- API: `GET /api/library` lists the library; `PUT` and `DELETE /api/sessions/{id}/sources/{source_id}`
  add a library file to a session or remove it; `DELETE /api/sources/{id}` deletes a file from
  the library; `PATCH /api/sources/{id}` also sets the title; new `/api/skills` routes.
  `DELETE /api/skills/{name}` deletes built-in skills too, and discarding changes to one is
  `POST /api/skills/{name}/reset`. `POST /api/skills/restore-builtins` and
  `POST /api/reset` (with `{"confirm": "NUKE"}`) are new, as is the `app.reset` event.

### Removed

- The "From library" dialog, replaced by the library list in the Sources tab, and
  `POST /api/sessions/{id}/sources/from-library`, replaced by `PUT` on the same resource.
- The `llm.history_budget_tokens` setting: the whole conversation is sent. A `raida.toml` that
  still sets it stops at startup and names the key to remove.

### Fixed

- Follow-ups could reach the model without the conversation they followed. When a session's
  notes nearly filled the prompt, as with five two-hour recordings, earlier turns were dropped,
  sometimes all of them. Older turns were also dropped past 6,000 tokens of conversation.
- Source cards were wider than the sidebar and cut off names and buttons.
- The status strip was redrawn on every streamed word of an answer.
- The last lines of `scripts/setup-mac.sh` ran `make ollama` and `make llm` instead of printing
  them.
- `make bench` failed after timing the model, because timing transcription needs a recording;
  `make bench MEDIA=<file>` now times both.
- A changed built-in skill whose file could not be read showed as unchanged, with no Reset,
  and could not be saved or deleted. It now shows as changed, so Reset, Delete and saving from
  the editor repair it. Exporting such a skill shows the reason instead of replacing the page
  with an error.

### Security

- A small `SKILL.md` with nested YAML aliases could expand to gigabytes of memory. raida now
  refuses YAML aliases and caps the size of the frontmatter.
- A web page from anywhere could read your library and transcripts through raida while it ran,
  by pointing its own host name at 127.0.0.1 (DNS rebinding). raida now answers only requests
  addressed to this Mac (`127.0.0.1`, `localhost`, `::1` or `server.host`). It also refuses any
  change sent by another site's page. Opening raida by the Mac's network name no longer works.

### Upgrade notes

- The database upgrades itself to schema version 6 on first start: each file used by several
  sessions becomes one library entry, and every session keeps its files. Back up
  `~/Library/Application Support/raida/raida.sqlite3` first: going back to an older version
  of raida needs that backup.
- Reload open browser tabs once after upgrading.

## [0.2.0] - 2026-09-25

### Added

- Notes when a file is added: sources longer than about 3,000 tokens are split into sections of
  about 25 minutes of speech and noted, with time codes or page anchors, in the background. On an
  M2 Max a two-hour recording is ready about 3.5 minutes after it is added.
- Answers from notes: short sources are read in full and long ones through their notes, plus
  passages of the full text that match the question. "Read full text" on a question reads every
  source word for word instead, condensing first when they do not fit.
- Read-ahead: with llama-server, each session's sources are read into the model's prompt cache
  when the session is opened, when its sources change and after each answer, so a question
  starts after reading only itself.
- `llama_server` backend, now the recommended one, and `make llm MODEL=<ollama tag>`, which
  starts llama.cpp's server on the weights Ollama downloaded, without copying them.
- Answers take priority: background work (notes, read-ahead, titles) pauses while an answer is
  being written and resumes afterwards.
- Chinese (Traditional, Taiwan), Chinese (Simplified) and Cantonese (Hong Kong) as source
  languages. Transcripts are normalized with OpenCC, which repairs the Apple engine's
  character conversion (外麵 for 外面). Answers, notes and session titles are held to the script
  of the instruction, or of the sources after an instruction in another language.
- Automatic session titles: a new session is named with the date and time, then renamed after
  its first answer in the language of the instruction. A title you set is never overwritten.
- Shared sources, first version: "From library" adds a file processed in another session, ready
  at once.
- `llm.think` turns reasoning before answers off, or sets its effort for gpt-oss.
- Each answer is labelled with how it was made (from notes, full text, condensed) and how many
  tokens were read and already cached; progress shows "Reading N tokens" and "Thinking (N
  tokens)" before the text starts.
- `docs/user-guide.md`, measurements for `qwen3:30b-a3b` on an M2 Max in `docs/benchmarks.md`,
  and `make ollama`.

### Changed

- Token estimates count Chinese, Japanese and Korean at about one token per character; they were
  under-counted almost threefold. raida refuses a prompt that would not fit the model's context
  instead of letting the server silently drop sources.
- Answers follow the language of the instruction, whatever language the sources are in.
- `llm.request_timeout_s` defaults to one hour, and a timeout names that setting.
- `raida.example.toml` uses llama-server on port 8080.
- The Makefile puts `/opt/homebrew/bin` first on PATH, so `make dev` finds `uv` in any terminal.
- The default Parakeet weights are `mlx-community/parakeet-tdt-0.6b-v3`; Ollama runs with
  `OLLAMA_NO_CLOUD=1`; setup uses the Apple Silicon Homebrew in `/opt/homebrew` explicitly.

### Fixed

- Every source stayed "Queued" at the default log level.
- `raida serve` crashed when the model was not ready, instead of starting with a warning.
- The default data folder was created as a folder literally named `~` inside the current folder.
- Media without speech failed instead of giving an empty transcript.
- With the Apple engine on "Auto-detect", recordings failed with a Hugging Face download error;
  they now ask for a language.
- The full regional language code (such as `zh-TW`) did not reach the transcriber, so
  Traditional Chinese came out Simplified; transcripts cached with the wrong language are
  discarded.
- Apple speech "Downloading required assets ... CancellationError" is retried up to three times.
- A Traditional Chinese question over long Mandarin transcripts got an English answer claiming
  the sources were irrelevant, because the prompt overflowed the model's context.
- A long prompt failed after 15 minutes with an empty error while the model was still reading.
- Browsers kept showing an old UI after an update.

### Upgrade notes

- The database upgrades itself to schema version 3 on first start.
- Re-run Traditional Chinese recordings transcribed before this version to get correct
  characters.

## [0.1.0] - 2026-09-15

### Added

- Local web app at `http://127.0.0.1:8765`: drop in sources, type an instruction, watch the
  answer stream in, export it. The model, speech-to-text and OCR run on the Mac; nothing leaves
  it.
- Sources: text, Markdown, PDF (scanned pages through Apple Vision OCR), Word, subtitles (.srt,
  .vtt) and any audio or video ffmpeg reads. "Add by path" references large local files in
  place, from folders listed in `paths.allowed_roots`.
- Sources process in parallel within per-resource limits (CPU pool, one transcription on the
  GPU at a time, one model call), and processed text is cached by content, so a file is never
  transcribed twice.
- Speech-to-text: Parakeet TDT 0.6B v3 (25 European languages), Whisper large-v3 (99
  languages, language detection) and the Apple engine through `yap` (macOS 26+). Language is
  detected automatically or set per source.
- Models through Ollama or any OpenAI-compatible server. Sources that fit the budget are read in
  one prompt; larger inputs are condensed source by source first (map-reduce).
- Export answers as .txt, .md, .pdf and .docx, or copy them.
- Sessions keep their sources, conversation and exports; follow-up instructions see earlier
  answers.
- Source cards show stage, progress and errors, with Re-run, Cancel and "View text" (with page
  anchors and time codes). "Run now with ready sources only" skips files still processing.
- Status strip and `raida doctor` check the model, transcription, OCR, PDF export and storage.
- Command line: `raida doctor`, `serve`, `process`, `ask` and `export`.
- Configuration in `raida.toml`, with `RAIDA_<SECTION>__<KEY>` environment overrides; only
  `llm.model` is required.
- `scripts/setup-mac.sh` installs everything on an Apple Silicon Mac and downloads the models.

[Unreleased]: https://github.com/llamamellama/raida/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/llamamellama/raida/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/llamamellama/raida/releases/tag/v0.1.0

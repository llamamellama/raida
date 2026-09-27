# Architecture

raida is a single-user, local-first application. One Python process (FastAPI on uvicorn, bound
to 127.0.0.1) serves a static web UI, a JSON API and a Server-Sent Events stream, and runs the
processing pipeline with asyncio. Models run outside the process (the LLM server, llama-server
by default) or in-process on MLX (speech-to-text). Decisions are recorded in `adr/`.

The shape of the system follows one measurement (ADR-0005): reading a prompt costs time that
grows faster than its length, so work that does not depend on the question happens when a file
is added, and each session's prompt prefix is read before the question arrives.

## Components

| Module | Responsibility |
| --- | --- |
| `raida.config` | TOML + environment configuration, fail-fast validation, derived paths |
| `raida.db` | SQLite schema and repository (sync, called via `asyncio.to_thread`) |
| `raida.api` | routes for sessions, the source library, messages, exports, skills, health; SSE stream |
| `raida.pipeline.scheduler` | per-source pipelines, per-message synthesis, resource limits, events |
| `raida.pipeline.stages` | text, pdf (+ OCR), media (ffmpeg), docx, subtitles |
| `raida.transcribe` | `Transcriber` protocol; Parakeet (MLX), Whisper (MLX), Apple engine, fake; OpenCC script normalization |
| `raida.llm` | `LlmBackend` protocol; llama-server, Ollama, OpenAI-compatible, fake; notes, retrieval, priority gate, prompts, chunking, synthesis |
| `raida.skills` | skills: Agent Skills folders (SKILL.md) built in and in `<data_dir>/skills`, import/export, `@name` parsing and rendering |
| `raida.script` | Chinese script detection and OpenCC conversion for transcripts, notes, answers and titles |
| `raida.export` | Markdown to txt / html / pdf (WeasyPrint or fpdf2) / docx |
| `raida.web` | vanilla JS UI with vendored `marked` and `DOMPurify` |
| `raida.doctor` | environment checks shared by the CLI and `/api/health` |

## Data model

- Session: a conversation over the library files it uses, with its messages and artifacts.
- Source: one file in the library, shared by every session (ADR-0007). `kind` (text, pdf,
  audio, video, docx, subtitles), `sha256` (unique: one entry per content), `original_name`
  (the file's name) and `title` (the name shown and given to the model, which cites sources by
  it; the user can change it, and it falls back to the file's name when cleared), `language`
  (`auto` or a code), `status`, `progress`, `processed_path`, `token_estimate`, `meta`. A session uses
  a source through a `session_sources` row (session, source, `added_at`); reads add `sessions`
  (how many use it) and `last_used_at`. Deleting a session leaves its sources in the library.
- Job: one stage run for a source (stage, resource class, state, timing, error).
- Message: user instruction or assistant answer. Assistant messages carry `status`
  (pending, waiting_for_sources, streaming, done, failed, cancelled), `strategy`
  (single_shot, notes, map_reduce), `full_text` and token usage. Both messages of a skill run
  carry `skill` (name, title, description, arguments, the filled-in instructions, options,
  revision hash); the user message also carries `prompt`, the full instruction sent.
- Skill: a folder, not a table row: `SKILL.md` plus optional `assets/example.md` and
  `references/reference.md` (ADR-0006). Built-in ones ship in the package; a user folder with
  the same name overrides one, and a deleted one is named in `skills/.deleted-builtins` until
  it is restored (ADR-0008).
- Artifact: an export of a message (format, path, filename).
- ProcessedCache: content hash + kind + language + pipeline variant -> processed markdown.
- Notes: overview plus anchored section notes of one processed document, keyed by processed
  key, model and notes version; shared by every session that has the file.
- Condensation: cached map-reduce intermediates keyed by source hash, instruction hash and model;
  also holds finished note sections, so an interrupted note run resumes.

Files live under the data directory: `uploads/<sha256>/<name>`, `media/<sha256>.wav`,
`processed/<key>.md` + `.json`, `artifacts/<message>/<artifact>.<ext>`, `skills/<name>/`,
`models/hf` (Hugging Face cache), `raida.sqlite3`.

## Source pipeline

```
queued -> [extracting | transcoding -> detecting_language -> transcribing | rendering -> ocr] -> noting -> ready
                                                                                                  \-> failed / cancelled
```

- text / docx / subtitles: one CPU-pool call.
- pdf: `TextExtractor` in the CPU pool; if the mean characters per page fall under
  `ocr.min_chars_per_page` (or too many pages are near-empty), pages are rendered at
  `ocr.dpi` and OCR'd page by page in the pool (Apple Vision via ocrmac). Output carries
  `[p. N]` anchors.
- audio / video: ffmpeg extracts 16 kHz mono WAV (subprocess slot); a 30-second clip goes
  through the language detector unless the language is set; the router picks Parakeet for its
  25 languages, Whisper otherwise; transcription runs in a worker thread under the GPU
  semaphore. Segments are coalesced into ~45-second paragraphs with `[hh:mm:ss]` anchors.
- Chinese transcripts for `zh-TW`, `zh-HK` and `zh-CN` are normalized with OpenCC (to
  Simplified, then to the regional standard), which repairs the character-by-character
  conversion of Apple's zh-TW recognizer (外麵 for 外面).
- noting: sources longer than `notes.min_source_tokens` are split into `notes.section_tokens`
  sections; each becomes anchored notes (reasoning off, background priority), then an overview
  is written from them. A failure (for example the model server is down) leaves the source
  ready without notes; answers then read it in full. At startup, ready sources without notes
  are noted one at a time.
- A file is processed once, as its library entry, whatever the number of sessions using it.
  Adding known content (upload, add by path, `raida process`) finds the entry: the file is ready
  at once, or the session shares the run in progress and its questions wait for it; the new
  upload's copy is discarded. Processed text is also cached by content, kind, language and
  pipeline variant, so switching a file back to an earlier language is immediate.
- Deleting a source from the library removes it from every session with its uploaded copy
  (never a file added by path), decoded audio, processed text, notes and condensations.

Resource classes (`raida.pipeline.resources`): a spawn-based `ProcessPoolExecutor` sized to the
performance cores (max 6) for CPU stages; `asyncio.Semaphore(1)` for the GPU; the LLM gate
(`raida.llm.gate`) for model calls; a semaphore for ffmpeg. The gate gives answers priority:
background calls (notes, reading sessions ahead, titles) wait while an answer is being written,
and one already running is cancelled and retried afterwards. Progress from stages reaches the UI as `job.progress` events;
state changes as `source.updated`.

## Synthesis

Fast path (default). `Synthesizer.plan_fast` builds a prompt prefix that does not depend on the
question: the system prompt, one `<source>` block per ready source, an acknowledgement, and the
whole conversation: every finished question and answer of the session, word for word (ADR-0010).
The sources take at most `llm.interactive_budget_tokens`, chosen without the conversation, so
they stay the same all session and each answer only adds to a cached prefix. When every source
fits in full, all go in full (`single_shot`). Otherwise short sources go in full and long ones
as their notes (`notes`); if the notes do not fit, the least relevant sources (BM25 of the
instruction against their notes) fall back to their overviews. The conversation comes on top, up
to `llm.synthesis_budget_tokens`; past that more sources fall back to overviews, and when even
that is not enough the question fails with a message to start a new session. The conversation
itself is never cut. A question whose answer failed or was stopped is left out. The final
user turn carries verbatim passages from the full text of the noted sources, selected with BM25
over paragraphs (character bigrams for CJK) within `llm.passage_budget_tokens`, then the
instruction. A long source without notes sends the message down the full-text path.

Full-text path ("Read full text", `full_text` on the message). The sum of the full sources, the
instruction, the system prompt and the whole conversation is compared with
`llm.synthesis_budget_tokens`. If
it fits, every source goes in verbatim (`single_shot`). Otherwise each oversized source is split
on headings and paragraphs into `llm.map_chunk_tokens` chunks, each chunk is condensed with the
instruction in view while keeping anchors, and notes are merged hierarchically until the source
fits its share of what the conversation leaves (`map_reduce`). Intermediates are cached so
re-asking is cheap.

In both paths the final call streams; deltas are appended to the message row and published as
`message.delta`. The answer follows the instruction's language (system prompt). For Chinese,
`raida.script.answer_script` also picks a target script: the one the instruction names, else
the one it is written in (judged by characters unique to one script), else, for an instruction
in another language, the sources' script; region from the sources' language codes. A Chinese
instruction gets a reminder in its own script appended, and `ScriptStream` converts the
streamed text with OpenCC at phrase breaks. The session title is converted the same way. Reasoning streamed by the server is not shown, but its token count is reported
as `message.progress` ("Thinking (N tokens)").

Reading ahead. With a backend that keeps a prompt cache (llama-server), the scheduler reads a
session's prefix into the cache in the background (`max_tokens: 0`) when the session is opened,
when its list of sources changes and settles, and after each answer, so the next question reads
only itself. When a file finishes processing, only the sessions open in a tab are read; a file
used by many sessions would otherwise queue a long read for each. Usage on each answer reports
`cached_tokens`.

Skills. `@name rest` at the start of an instruction runs a skill (ADR-0006): the scheduler
renders it when the message is submitted (instructions with `$ARGUMENTS` filled, then the
reference and the output example, each introduced for what it is) and stores the result on the
message. The rendered text is the instruction of the final user turn, after the cached prefix.
Passages are searched with what the user typed after the command, else the skill's
instructions. The skill's language option picks the answer language (its instructions, the
main language of the sources weighted by length, or a set language), its think option can turn
reasoning off, and its full-text option takes the full-text path. In later history a skill run
appears as its command and one line of description.

A question whose session is being read
ahead waits for that read instead of cancelling it: cancelling made the question land on
another server slot and re-read the whole prefix.

A prompt submitted while sources are still processing waits (`waiting_for_sources`) and starts
when the last source finishes, unless `run_with_ready_only` was set.

## API

See the route modules for request and response models. Event types on
`GET /api/sessions/{id}/events`: `snapshot` (the session, its sources, messages and artifacts,
and the whole library, on connect), `app.version` (the hash of the UI files the server serves),
`system.status`, `source.updated`, `source.removed` and `job.progress` (sent to every tab, since
any session may list the file), `session.sources` (the ids of the session's sources, in order,
when that list changes), `message.updated`, `message.delta`, `message.progress`,
`artifact.created`, `session.updated`, `skills.updated` (sent to every tab when a skill
changes), and `app.reset` (sent to every tab after a factory reset; tabs reload). Reconnects resync from the snapshot. Sources: `GET /api/library`; `POST
/api/sessions/{id}/sources` (upload) and `.../sources/by-path` put files in the library and the
session; `PUT` and `DELETE /api/sessions/{id}/sources/{source_id}` add a library file to a
session or remove it; `GET /api/sources/{id}`, `/text`, `/notes`, `PATCH` (title, language), `/retry`,
`/cancel`; `DELETE /api/sources/{id}` deletes from the library. Skills: `GET/POST /api/skills`,
`GET/PUT/DELETE /api/skills/{name}` (DELETE works on built-in skills too),
`POST /api/skills/{name}/reset`, `POST /api/skills/restore-builtins`, `POST /api/skills/import`,
`GET /api/skills/{name}/export`. `POST /api/reset` with `{"confirm": "NUKE"}` stops all work
and deletes everything the user made (ADR-0008).
Every request must be addressed to this machine (Host `127.0.0.1`, `localhost`, `::1` or
`server.host`), and a POST, PUT, PATCH or DELETE carrying another site's Origin is refused; that
stops a web page from using raida through DNS rebinding (ADR-0009).
The server reads the UI files once, when it starts, and serves only those: files edited on disk
while it runs are served after a restart, together with the server code that matches them (a
page once got newer scripts than its server and could not list the sessions). The page loads
its scripts from `/static/<content hash>/`, so a browser never mixes modules of two versions,
and carries that hash; a tab that reconnects to a server with other UI files reloads itself,
keeping the text in the instruction box.

## Failure handling

Errors surface as source or message `error` text in the UI with a Re-run action; nothing is
retried silently except one transcription retry on transient errors. The app refuses to start
without ffmpeg, a writable data directory or a PDF renderer; a missing or unloaded LLM is a
visible warning so sources can still be processed. Logs are structured JSON on stdout.

## Testing

Unit tests cover config, chunking, token estimation, transcript formatting, script
normalization, retrieval, the priority gate, request shaping of each LLM backend (with a mock
transport), the skill format, store, archive import, command parsing and rendering, how a skill
picks its answer language, the fast-path planner, PDF extraction and the OCR heuristic, every
exporter, and the schema upgrades (including per-session rows becoming one library). Integration
tests run the real app with the fake LLM and fake transcriber through httpx, including uploads
of every kind, the library (a file shared by sessions, processed once even when two sessions add
it during its run, kept when its session is deleted, removed everywhere when deleted from the
library), cache hits, add-by-path rules, waiting-for-sources, notes taken once and shared
across sessions, answers from notes with retrieved passages, reading ahead (and a question
waiting for its session's read-ahead), skills (managing, importing, exporting, running in
several sessions, options, follow-ups, the CLI), map-reduce, exports, and the SSE stream against
a live uvicorn server, the factory reset (what it deletes and keeps, work in progress
stopped, every tab told) and the refusal of requests for other host names or from other
sites.
Real-model smoke tests are a documented manual step on the Mac (`make bench` and the checklist
in `docs/model-setup.md`).

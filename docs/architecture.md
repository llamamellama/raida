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
| `raida.api` | routes for sessions, sources, messages, exports, health; SSE stream |
| `raida.pipeline.scheduler` | per-source pipelines, per-message synthesis, resource limits, events |
| `raida.pipeline.stages` | text, pdf (+ OCR), media (ffmpeg), docx, subtitles |
| `raida.transcribe` | `Transcriber` protocol; Parakeet (MLX), Whisper (MLX), Apple engine, fake; OpenCC script normalization |
| `raida.llm` | `LlmBackend` protocol; llama-server, Ollama, OpenAI-compatible, fake; notes, retrieval, priority gate, prompts, chunking, synthesis |
| `raida.export` | Markdown to txt / html / pdf (WeasyPrint or fpdf2) / docx |
| `raida.web` | vanilla JS UI with vendored `marked` and `DOMPurify` |
| `raida.doctor` | environment checks shared by the CLI and `/api/health` |

## Data model

- Session: workspace with sources, messages and artifacts.
- Source: one file. `kind` (text, pdf, audio, video, docx, subtitles), `sha256`, `language`
  (`auto` or a code), `status`, `progress`, `processed_path`, `token_estimate`, `meta`.
- Job: one stage run for a source (stage, resource class, state, timing, error).
- Message: user instruction or assistant answer. Assistant messages carry `status`
  (pending, waiting_for_sources, streaming, done, failed, cancelled), `strategy`
  (single_shot, map_reduce) and token usage.
- Artifact: an export of a message (format, path, filename).
- ProcessedCache: content hash + kind + language + pipeline variant -> processed markdown.
- Notes: overview plus anchored section notes of one processed document, keyed by processed
  key, model and notes version; shared by every session that has the file.
- Condensation: cached map-reduce intermediates keyed by source hash, instruction hash and model;
  also holds finished note sections, so an interrupted note run resumes.

Files live under the data directory: `uploads/<sha256>/<name>`, `media/<sha256>.wav`,
`processed/<key>.md` + `.json`, `artifacts/<message>/<artifact>.<ext>`, `models/hf` (Hugging
Face cache), `raida.sqlite3`.

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
- Every result is stored once per content hash and reused across sessions.

Resource classes (`raida.pipeline.resources`): a spawn-based `ProcessPoolExecutor` sized to the
performance cores (max 6) for CPU stages; `asyncio.Semaphore(1)` for the GPU; the LLM gate
(`raida.llm.gate`) for model calls; a semaphore for ffmpeg. The gate gives answers priority:
background calls (notes, reading sessions ahead, titles) wait while an answer is being written,
and one already running is cancelled and retried afterwards. Progress from stages reaches the UI as `job.progress` events;
state changes as `source.updated`.

## Synthesis

Fast path (default). `Synthesizer.plan_fast` builds a prompt prefix that does not depend on the
question: the system prompt, one `<source>` block per ready source, an acknowledgement, and the
most recent history that fits `llm.history_budget_tokens`. When every source fits
`llm.interactive_budget_tokens` in full, all go in full (`single_shot`). Otherwise short sources
go in full and long ones as their notes (`notes`); if the notes do not fit, the least relevant
sources (BM25 of the instruction against their notes) fall back to their overviews. The final
user turn carries verbatim passages from the full text of the noted sources, selected with BM25
over paragraphs (character bigrams for CJK) within `llm.passage_budget_tokens`, then the
instruction. A long source without notes sends the message down the full-text path.

Full-text path ("Read full text", `full_text` on the message). The sum of the full sources, the
instruction, the system prompt and history is compared with `llm.synthesis_budget_tokens`. If
it fits, every source goes in verbatim (`single_shot`). Otherwise each oversized source is split
on headings and paragraphs into `llm.map_chunk_tokens` chunks, each chunk is condensed with the
instruction in view while keeping anchors, and notes are merged hierarchically until the source
fits its share (`map_reduce`). Intermediates are cached so re-asking is cheap.

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
when its sources settle and after each answer, so the next question reads only itself. Usage on
each answer reports `cached_tokens`.

A prompt submitted while sources are still processing waits (`waiting_for_sources`) and starts
when the last source finishes, unless `run_with_ready_only` was set.

## API

See the route modules for request and response models. Event types on
`GET /api/sessions/{id}/events`: `snapshot` (full session state on connect), `system.status`,
`source.updated`, `source.removed`, `job.progress`, `message.updated`, `message.delta`,
`message.progress`, `artifact.created`, `session.updated`. Reconnects resync from the snapshot.

## Failure handling

Errors surface as source or message `error` text in the UI with a Re-run action; nothing is
retried silently except one transcription retry on transient errors. The app refuses to start
without ffmpeg, a writable data directory or a PDF renderer; a missing or unloaded LLM is a
visible warning so sources can still be processed. Logs are structured JSON on stdout.

## Testing

Unit tests cover config, chunking, token estimation, transcript formatting, script
normalization, retrieval, the priority gate, request shaping of each LLM backend (with a mock
transport), PDF extraction and the OCR heuristic, and every exporter. Integration tests run the real app with the fake LLM and
fake transcriber through httpx, including uploads of every kind, cache hits, add-by-path rules,
waiting-for-sources, notes taken once and shared across sessions, answers from notes with
retrieved passages, reading ahead, map-reduce, exports, and the SSE stream against a live
uvicorn server.
Real-model smoke tests are a documented manual step on the Mac (`make bench` and the checklist
in `docs/model-setup.md`).

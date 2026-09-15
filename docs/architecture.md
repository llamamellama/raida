# Architecture

raida is a single-user, local-first application. One Python process (FastAPI on uvicorn, bound
to 127.0.0.1) serves a static web UI, a JSON API and a Server-Sent Events stream, and runs the
processing pipeline with asyncio. Models run outside the process (the LLM server) or in-process
on MLX (speech-to-text). Decisions are recorded in `adr/`.

## Components

| Module | Responsibility |
| --- | --- |
| `raida.config` | TOML + environment configuration, fail-fast validation, derived paths |
| `raida.db` | SQLite schema and repository (sync, called via `asyncio.to_thread`) |
| `raida.api` | routes for sessions, sources, messages, exports, health; SSE stream |
| `raida.pipeline.scheduler` | per-source pipelines, per-message synthesis, resource limits, events |
| `raida.pipeline.stages` | text, pdf (+ OCR), media (ffmpeg), docx, subtitles |
| `raida.transcribe` | `Transcriber` protocol; Parakeet (MLX), Whisper (MLX), Apple engine, fake |
| `raida.llm` | `LlmBackend` protocol; Ollama, OpenAI-compatible, fake; prompts, chunking, synthesis |
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
- Condensation: cached map-reduce intermediates keyed by source hash, instruction hash and model.

Files live under the data directory: `uploads/<sha256>/<name>`, `media/<sha256>.wav`,
`processed/<key>.md` + `.json`, `artifacts/<message>/<artifact>.<ext>`, `models/hf` (Hugging
Face cache), `raida.sqlite3`.

## Source pipeline

```
queued -> [extracting | transcoding -> detecting_language -> transcribing | rendering -> ocr] -> ready
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
- Every result is stored once per content hash and reused across sessions.

Resource classes (`raida.pipeline.resources`): a spawn-based `ProcessPoolExecutor` sized to the
performance cores (max 6) for CPU stages; `asyncio.Semaphore(1)` for the GPU; a semaphore for
LLM calls; a semaphore for ffmpeg. Progress from stages reaches the UI as `job.progress` events;
state changes as `source.updated`.

## Synthesis

`Synthesizer.plan` sums the token estimates of ready sources, the instruction, the system prompt
and the most recent history that fits `llm.history_budget_tokens`. If the total fits
`llm.synthesis_budget_tokens` the prompt contains every source verbatim in `<source>` blocks
(single_shot). Otherwise each oversized source is split on headings and paragraphs into
`llm.map_chunk_tokens` chunks, each chunk is condensed with the instruction in view while
keeping anchors, and notes are merged hierarchically until the source fits its share
(map_reduce). The final call streams; deltas are appended to the message row and published as
`message.delta`. Intermediates are cached so re-asking is cheap.

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

Unit tests cover config, chunking, token estimation, transcript formatting, PDF extraction and
the OCR heuristic, and every exporter. Integration tests run the real app with the fake LLM and
fake transcriber through httpx, including uploads of every kind, cache hits, add-by-path rules,
waiting-for-sources, map-reduce, exports, and the SSE stream against a live uvicorn server.
Real-model smoke tests are a documented manual step on the Mac (`make bench` and the checklist
in `docs/model-setup.md`).

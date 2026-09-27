"""Runs source pipelines and synthesis with per-resource concurrency limits."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any

from raida.config import Config
from raida.db import Database
from raida.db.repo import NotFoundError
from raida.doctor import find_ffmpeg
from raida.llm.notes import NoteTaker
from raida.llm.registry import build_llm_backend
from raida.llm.synthesis import Synthesizer
from raida.llm.tokens import estimate_tokens
from raida.models import (
    ACTIVE_SOURCE_STATUSES,
    TERMINAL_SOURCE_STATUSES,
    HealthReport,
    Message,
    ProcessedSource,
    ResetSummary,
    SessionDetail,
    SessionSnapshot,
    Source,
    SourceNotes,
    new_id,
    utc_now,
)
from raida.pipeline import cache, ingest
from raida.pipeline.events import Event, EventBus
from raida.pipeline.resources import ResourcePool
from raida.pipeline.stages import docx, media, ocr, pdf, subtitles, text
from raida.script import normalize_script
from raida.skills import SkillNotFoundError, SkillStore, parse_command, render, unescape
from raida.transcribe.base import Transcript
from raida.transcribe.format import transcript_markdown
from raida.transcribe.languages import base_language
from raida.transcribe.registry import TranscriberRegistry

log = logging.getLogger(__name__)

# How long a question waits for its session's read-ahead to finish. That read is the prefix the
# question needs; cancelling it made the question start over on another server slot.
PREPARE_WAIT_S = 900.0


class ResetInProgressError(RuntimeError):
    """A factory reset is deleting everything; nothing new starts until it has finished."""


class Scheduler:
    def __init__(self, config: Config, db: Database) -> None:
        self.config = config
        self.db = db
        self.events = EventBus()
        self.resources = ResourcePool(config)
        self.transcribers = TranscriberRegistry(config)
        self.llm = build_llm_backend(config)
        self.synthesizer = Synthesizer(config, db, self.llm, self.resources.llm)
        self.notes = NoteTaker(config, db, self.llm, self.resources.llm)
        self.skills = SkillStore(config.skills_dir)
        self._source_tasks: dict[str, asyncio.Task[None]] = {}
        self._prepare_tasks: dict[str, asyncio.Task[None]] = {}
        self._message_tasks: dict[str, asyncio.Task[None]] = {}
        self._background: set[asyncio.Task[Any]] = set()
        self._stopping = False
        self._resetting = False
        self._reset_lock = asyncio.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None
        self.last_health: HealthReport | None = None

    @property
    def _halted(self) -> bool:
        """Shutting down or resetting: cancelled work must not start new work as it ends."""
        return self._stopping or self._resetting

    def _check_open(self) -> None:
        if self._resetting:
            raise ResetInProgressError("raida is being reset to factory settings; try again")

    # -- lifecycle -------------------------------------------------------------------------

    async def start(self) -> None:
        self._loop = asyncio.get_running_loop()
        self.resources.start()
        for source in await asyncio.to_thread(self.db.unfinished_sources):
            log.info("requeue_source", extra={"source_id": source.id, "status": source.status})
            await self._set_source(source.id, status="queued", progress=0.0, error=None)
            self.submit_source(source.id)
        self._spawn(self._warm_up_llm())
        self._spawn(self._backfill_notes())

    async def stop(self) -> None:
        self._stopping = True  # cancelled pipelines must not schedule new background work
        tasks = [
            *self._source_tasks.values(),
            *self._message_tasks.values(),
            *self._prepare_tasks.values(),
            *self._background,
        ]
        for task in tasks:
            task.cancel()
        for task in tasks:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        self.resources.shutdown()
        await self.llm.aclose()

    async def factory_reset(self) -> ResetSummary:
        """Nuke: stop all work and delete everything the user made (every session with its
        answers and exports, every library file with its transcript and notes, the user's
        skills and deleted built-in ones, database backups), leaving raida as installed. The
        settings file, the downloaded models and files added by path are kept."""
        async with self._reset_lock:
            self._resetting = True
            try:
                await self._cancel_all_work()
                skills = sum(
                    1
                    for info in (await asyncio.to_thread(self.skills.list))[0]
                    if info.origin != "builtin"
                )
                db_before = await asyncio.to_thread(self._db_bytes)
                counts = await asyncio.to_thread(self.db.wipe)
                freed = await asyncio.to_thread(ingest.remove_user_data, self.config)
                freed += max(0, db_before - await asyncio.to_thread(self._db_bytes))
            finally:
                self._resetting = False
        summary = ResetSummary(**counts, skills=skills, bytes_freed=freed)
        log.warning("factory_reset", extra=summary.model_dump())
        # Every tab reloads: the sessions they show are gone.
        self.events.publish(Event("app.reset", summary.model_dump()))
        return summary

    async def _cancel_all_work(self) -> None:
        """Cancel every pipeline, answer, read-ahead and background task and wait for them. A
        cancelled pipeline could start a question waiting for it as it ends; _halted prevents
        that, and the loop catches anything started meanwhile."""
        for _ in range(10):
            tasks = [
                t
                for t in (
                    *self._source_tasks.values(),
                    *self._message_tasks.values(),
                    *self._prepare_tasks.values(),
                    *self._background,
                )
                if not t.done()
            ]
            if not tasks:
                return
            for task in tasks:
                task.cancel()
            for task in tasks:
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await task
        log.warning("reset_tasks_left_running")

    def _db_bytes(self) -> int:
        path = self.config.db_path
        files = (path, path.with_name(path.name + "-wal"), path.with_name(path.name + "-shm"))
        return sum(f.stat().st_size for f in files if f.exists())

    def _spawn(self, coro: Any) -> asyncio.Task[Any]:
        task = asyncio.create_task(coro)
        self._background.add(task)
        task.add_done_callback(self._background.discard)
        return task

    async def _warm_up_llm(self) -> None:
        try:
            status = await self.llm.warm_up(self.config.llm.num_ctx)
            log.info("llm_warm", extra={"detail": status.detail})
            if self.last_health is not None:
                report = self.last_health.model_copy(update={"llm": status})
                report.ok = report.ok or (status.ok and report.transcriber.ok)
                self.publish_health(report)
        except Exception as exc:
            log.warning("llm_warm_up_failed", extra={"error": str(exc)[:300]})

    def publish_health(self, report: HealthReport) -> None:
        self.last_health = report
        self.events.publish(Event("system.status", report.model_dump()))

    # -- snapshot for SSE ------------------------------------------------------------------

    def snapshot(self, session_id: str) -> SessionDetail:
        return SessionDetail(
            session=self.db.get_session(session_id),
            sources=self.db.list_sources(session_id),
            messages=self.db.list_messages(session_id),
            artifacts=self.db.list_artifacts(session_id),
        )

    def stream_snapshot(self, session_id: str) -> SessionSnapshot:
        """What a tab receives when it connects: the session, and the library to choose from."""
        return SessionSnapshot(**dict(self.snapshot(session_id)), library=self.db.list_library())

    async def delete_session(self, session_id: str) -> None:
        """Delete a session with its conversation and exports. Its files stay in the library
        for the other sessions and for new ones."""
        messages = await asyncio.to_thread(self.db.list_messages, session_id)
        tasks = [self._message_tasks.get(m.id) for m in messages]
        tasks.append(self._prepare_tasks.get(session_id))
        for task in tasks:
            if task is not None and not task.done():
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await task
        used = await asyncio.to_thread(self.db.session_source_ids, session_id)
        await asyncio.to_thread(self.db.delete_session, session_id)
        for source_id in used:  # their use counts changed
            source = await asyncio.to_thread(self.db.get_source, source_id)
            self.events.publish(Event("source.updated", source.model_dump()))

    # -- the library, and the files each session uses ----------------------------------------

    async def add_source(self, session_id: str, candidate: Source) -> Source:
        """Put a file in the library, or find it there by its content, and use it in the
        session. A file already in the library is not processed again: dropped into a second
        session it is ready at once, or shares the run in progress. One that failed or was
        cancelled runs again, with the language given now when one is given."""
        self._check_open()
        await asyncio.to_thread(self.db.get_session, session_id)
        source, created = await asyncio.to_thread(self.db.add_to_library, candidate)
        if not created:
            source = await self._reuse(source, candidate)
        # Attached before the pipeline starts, so the session hears when it finishes.
        source = await self.attach_source(session_id, source.id)
        if created:
            log.info("source_added", extra={"source_id": source.id, "kind": source.kind})
            self.submit_source(source.id)
        return source

    async def _reuse(self, existing: Source, candidate: Source) -> Source:
        """The library has this content already. Keep its copy (the new upload is deleted),
        unless that copy is gone and the new one can replace it; a run that failed or was
        cancelled takes the language given now."""
        fields: dict[str, Any] = {}
        if await asyncio.to_thread(Path(existing.stored_path).is_file):
            await asyncio.to_thread(
                ingest.discard_upload, self.config, Path(candidate.stored_path), existing
            )
        else:
            fields.update(stored_path=candidate.stored_path, managed=candidate.managed)
        if existing.status in ("failed", "cancelled") and base_language(candidate.language):
            fields["language"] = candidate.language
        return await self._set_source(existing.id, **fields) if fields else existing

    async def attach_source(self, session_id: str, source_id: str) -> Source:
        """Use a library file in a session. Nothing is processed again, except a file whose
        run failed or was cancelled: asking for it here runs it again."""
        self._check_open()
        await asyncio.to_thread(self.db.get_session, session_id)
        source = await asyncio.to_thread(self.db.get_source, source_id)
        added = await asyncio.to_thread(self.db.attach_source, session_id, source_id)
        if source.status in ("failed", "cancelled"):
            await self.retry_source(source_id)
        if added:
            await self._uses_changed(session_id, source_id)
        return await asyncio.to_thread(self.db.get_source, source_id)

    async def detach_source(self, session_id: str, source_id: str) -> None:
        """Stop using a file in a session. It stays in the library (and keeps processing)."""
        if not await asyncio.to_thread(self.db.detach_source, session_id, source_id):
            raise NotFoundError(f"session {session_id} does not use source {source_id}")
        await self._uses_changed(session_id, source_id)

    async def delete_source(self, source_id: str) -> None:
        """Remove a file from the library and from every session that uses it, with its
        uploaded copy, decoded audio, processed text and notes."""
        source = await self.cancel_source(source_id)
        sessions = await asyncio.to_thread(self.db.sessions_using, source_id)
        await asyncio.to_thread(self.db.delete_source, source_id)
        processed = await asyncio.to_thread(self.db.forget_processed, source.sha256)
        await asyncio.to_thread(ingest.remove_file_data, self.config, source, processed)
        log.info("source_deleted", extra={"source_id": source_id, "sessions": len(sessions)})
        self.events.publish(Event("source.removed", {"source_id": source_id}))
        for session_id in sessions:
            await self._uses_changed(session_id, None)

    async def _uses_changed(self, session_id: str, source_id: str | None) -> None:
        """A session started or stopped using a file: its tabs get the new list, every tab the
        file's new use count; waiting questions may start, and the session is read ahead."""
        if source_id is not None:  # first, so tabs know the file before the list names it
            source = await asyncio.to_thread(self.db.get_source, source_id)
            self.events.publish(Event("source.updated", source.model_dump()))
        ids = await asyncio.to_thread(self.db.session_source_ids, session_id)
        self.events.publish(
            Event(
                "session.sources",
                {"session_id": session_id, "source_ids": ids},
                session_id=session_id,
            )
        )
        await self._maybe_start_waiting_messages(session_id)
        await self._session_settled(session_id)

    # -- source control --------------------------------------------------------------------

    def submit_source(self, source_id: str) -> None:
        if self._halted:
            return
        if source_id in self._source_tasks and not self._source_tasks[source_id].done():
            return
        task = asyncio.create_task(self._run_source(source_id), name=f"source:{source_id}")
        self._source_tasks[source_id] = task
        task.add_done_callback(lambda t: self._source_tasks.pop(source_id, None))

    async def cancel_source(self, source_id: str) -> Source:
        task = self._source_tasks.get(source_id)
        if task is not None and not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        source = await asyncio.to_thread(self.db.get_source, source_id)
        if source.status not in TERMINAL_SOURCE_STATUSES:
            source = await self._set_source(source_id, status="cancelled", error=None)
        return source

    async def retry_source(self, source_id: str) -> Source:
        self._check_open()
        await self.cancel_source(source_id)
        source = await self._set_source(
            source_id, status="queued", progress=0.0, error=None, processed_path=None
        )
        self.submit_source(source_id)
        return source

    async def rename_source(self, source_id: str, title: str) -> Source:
        """Give a file the name shown in every session and given to the model, which cites
        sources by name. An empty name brings back the file's own name. Nothing is processed
        again; the open sessions using the file read their new prompt prefix ahead."""
        source = await asyncio.to_thread(self.db.get_source, source_id)
        title = " ".join(title.split()) or source.original_name
        if title == source.title:
            return source
        source = await self._set_source(source_id, title=title)
        for session_id in await asyncio.to_thread(self.db.sessions_using, source_id):
            await self._session_settled(session_id, watched_only=True)
        return source

    async def set_language(self, source_id: str, language: str) -> Source:
        source = await asyncio.to_thread(self.db.get_source, source_id)
        if source.language == language:
            return source
        source = await self._set_source(source_id, language=language)
        if source.kind in ("audio", "video", "pdf"):
            return await self.retry_source(source_id)
        return source

    async def _set_source(self, source_id: str, **fields: Any) -> Source:
        # Library events go to every tab: any session may list the file.
        source = await asyncio.to_thread(self.db.update_source, source_id, **fields)
        self.events.publish(Event("source.updated", source.model_dump()))
        return source

    def _progress_reporter(
        self, source: Source, stage: str, lo: float, hi: float
    ) -> Callable[[float], None]:
        loop = self._loop

        def report(fraction: float) -> None:
            fraction = max(0.0, min(1.0, fraction))
            value = lo + (hi - lo) * fraction
            event = Event(
                "job.progress",
                {"source_id": source.id, "stage": stage, "progress": round(value, 3)},
            )
            if loop is not None and loop.is_running():
                loop.call_soon_threadsafe(self.events.publish, event)

        return report

    # -- source pipeline -------------------------------------------------------------------

    async def _run_source(self, source_id: str) -> None:
        source = await asyncio.to_thread(self.db.get_source, source_id)
        started = utc_now()
        try:
            # Inside the try so that any unexpected error marks the source failed instead of
            # leaving it queued forever. "name" is reserved by logging.LogRecord.
            log.info(
                "source_start",
                extra={
                    "source_id": source.id,
                    "kind": source.kind,
                    "source_name": source.original_name,
                },
            )
            doc, variant = await self._process(source)
            key = cache.cache_key(source.sha256, source.kind, source.language, variant)
            md_path = await asyncio.to_thread(
                cache.store_processed, self.config, self.db, key, source.sha256, doc
            )
            fields: dict[str, Any] = {
                "error": None,
                "processed_path": str(md_path),
                "token_estimate": doc.token_estimate,
                "meta": doc.meta,
            }
            if self.notes.needed(doc):
                source = await self._set_source(source.id, status="noting", progress=0.0, **fields)
                fields["meta"] = await self._take_notes(source, key, doc)
            await self._set_source(source.id, status="ready", progress=1.0, **fields)
            log.info(
                "source_ready",
                extra={"source_id": source.id, "tokens": doc.token_estimate, "started": started},
            )
        except asyncio.CancelledError:
            await self._set_source(source.id, status="cancelled")
            raise
        except Exception as exc:
            log.exception("source_failed", extra={"source_id": source.id})
            await self._set_source(
                source.id, status="failed", error=f"{type(exc).__name__}: {exc}"[:1000]
            )
        finally:
            await self._source_finished(source.id)

    async def _take_notes(
        self, source: Source, processed_key: str, doc: ProcessedSource
    ) -> dict[str, Any]:
        """Take (or load) the notes for a processed document; returns the source meta with a
        ``notes`` entry. A failure leaves the source usable through its full text."""
        await asyncio.to_thread(
            self.db.create_job, stage="noting", resource_class="llm", source_id=source.id
        )
        report = self._progress_reporter(source, "noting", 0.0, 0.99)
        language = source.language if base_language(source.language) else None
        try:
            notes = await self.notes.ensure(
                processed_key, doc, language, lambda done, total: report(done / max(1, total))
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.warning("notes_failed", extra={"source_id": source.id, "error": str(exc)[:300]})
            return {**doc.meta, "notes": {"status": "failed", "error": str(exc)[:500]}}
        return {**doc.meta, "notes": _notes_meta(notes)}

    async def _backfill_notes(self) -> None:
        """Take notes for ready sources processed before notes existed, or whose notes failed
        (for example while the model server was down). One source at a time, as background
        work; each run is registered like a source pipeline so Cancel and Remove reach it."""
        try:
            sources = await asyncio.to_thread(self.db.ready_sources)
        except Exception:
            log.exception("notes_backfill_failed")
            return
        for source in sources:
            if source.id in self._source_tasks:
                continue
            task = asyncio.create_task(self._backfill_one(source), name=f"notes:{source.id}")
            self._source_tasks[source.id] = task
            task.add_done_callback(lambda t, sid=source.id: self._source_tasks.pop(sid, None))
            try:
                await task
            except asyncio.CancelledError:
                current = asyncio.current_task()
                if current is not None and current.cancelling():
                    raise  # shutting down; the user cancelling one source only stops that one

    async def _backfill_one(self, source: Source) -> None:
        path = source.processed_path
        if not path:
            return
        key = Path(path).stem
        try:
            existing = await self.notes.load(key)
            if existing is not None:
                if (source.meta.get("notes") or {}).get("status") != "done":
                    # Taken already, for example before the library replaced per-session rows.
                    await self._set_source(
                        source.id, meta={**source.meta, "notes": _notes_meta(existing)}
                    )
                return
            if not await asyncio.to_thread(Path(path).exists):
                return
            doc = await asyncio.to_thread(
                cache.load_processed,
                path,
                source.id,
                source.title,
                self.config.llm.chars_per_token,
            )
            if not self.notes.needed(doc):
                return
            current = await asyncio.to_thread(self.db.get_source, source.id)
            if current.status != "ready":
                return
            current = await self._set_source(source.id, status="noting", progress=0.0)
            meta = await self._take_notes(current, key, doc)
            await self._set_source(source.id, status="ready", progress=1.0, meta=meta)
        except NotFoundError:
            return
        except asyncio.CancelledError:
            # The text is still ready; only the notes were interrupted.
            with contextlib.suppress(Exception):
                await self._set_source(source.id, status="ready", progress=1.0)
            raise
        except Exception:
            log.exception("notes_backfill_failed", extra={"source_id": source.id})
            return
        await self._source_finished(source.id)

    async def _source_finished(self, source_id: str) -> None:
        """A file finished processing or noting: questions waiting for it in any session may
        start, and the open sessions that use it are read ahead."""
        sessions = await asyncio.to_thread(self.db.sessions_using, source_id)
        for session_id in sessions:
            await self._maybe_start_waiting_messages(session_id)
            await self._session_settled(session_id, watched_only=True)

    async def _session_settled(self, session_id: str, *, watched_only: bool = False) -> None:
        """Read the session ahead once none of its sources is still processing. When a file
        finishes, only sessions open in a tab are read (the others are read when opened), so a
        file used in many sessions does not queue a long read for each of them."""
        if watched_only and not self.events.watching(session_id):
            return
        try:
            sources = await asyncio.to_thread(self.db.list_sources, session_id)
        except NotFoundError:
            return
        if not any(s.status in ACTIVE_SOURCE_STATUSES for s in sources):
            self.schedule_prepare(session_id)

    # -- prompt cache ----------------------------------------------------------------------

    def schedule_prepare(self, session_id: str) -> None:
        """Read the session's sources into the model server's prompt cache in the background,
        so the next question starts answering after reading only itself. Called when a session
        is opened, when its sources settle and after each answer."""
        if not self.llm.prompt_cache or self._halted:
            return
        running = self._prepare_tasks.get(session_id)
        if running is not None and not running.done():
            running.cancel()  # its sources or history changed; prepare the new prefix
        task = asyncio.create_task(self._prepare(session_id), name=f"prepare:{session_id}")
        self._prepare_tasks[session_id] = task
        task.add_done_callback(
            lambda t: (
                self._prepare_tasks.pop(session_id, None)
                if self._prepare_tasks.get(session_id) is t
                else None
            )
        )

    async def _prepare(self, session_id: str) -> None:
        try:
            sources = await asyncio.to_thread(self.db.list_sources, session_id)
            if any(s.status in ACTIVE_SOURCE_STATUSES for s in sources):
                return
            messages = await asyncio.to_thread(self.db.list_messages, session_id)
            if any(m.status in ("pending", "streaming") for m in messages):
                return
            docs, notes = await self._load_docs(sources)
            if not docs:
                return
            plan = self.synthesizer.plan_fast(docs, notes, messages)
            if plan is None or "overview" in plan.forms.values():
                # Without notes for every long source, or with some sources cut to overviews to
                # fit llm.interactive_budget_tokens, the prompt depends on the question.
                log.info(
                    "session_prepare_skipped",
                    extra={
                        "session_id": session_id,
                        "reason": "long source without notes" if plan is None else "overviews",
                    },
                )
                return
            started = asyncio.get_running_loop().time()
            usage = await self.synthesizer.prefill(plan)
            if usage is not None:
                log.info(
                    "session_prepared",
                    extra={
                        "session_id": session_id,
                        "prompt_tokens": usage.prompt_tokens,
                        "cached_tokens": usage.cached_tokens,
                        "seconds": round(asyncio.get_running_loop().time() - started, 1),
                    },
                )
        except asyncio.CancelledError:
            raise
        except NotFoundError:
            return
        except Exception as exc:
            log.warning(
                "session_prepare_failed", extra={"session_id": session_id, "error": str(exc)[:300]}
            )

    async def _load_docs(
        self, sources: list[Source]
    ) -> tuple[list[ProcessedSource], dict[str, SourceNotes]]:
        ready = [s for s in sources if s.status == "ready" and s.processed_path]
        cpt = self.config.llm.chars_per_token
        docs: list[ProcessedSource] = []
        notes: dict[str, SourceNotes] = {}
        for s in ready:
            path = s.processed_path or ""
            docs.append(await asyncio.to_thread(cache.load_processed, path, s.id, s.title, cpt))
            note = await self.notes.load(Path(path).stem)
            if note is not None:
                notes[s.id] = note
        return docs, notes

    async def _stage(self, source: Source, stage: str, resource: str, progress: float) -> None:
        await self._set_source(source.id, status=stage, progress=progress)
        await asyncio.to_thread(
            self.db.create_job, stage=stage, resource_class=resource, source_id=source.id
        )

    def _variant(self, source: Source) -> str:
        if source.kind == "pdf":
            ocr_cfg = self.config.ocr
            langs = ",".join(ocr_cfg.languages)
            return f"{self.config.pdf.extractor}|ocr={ocr_cfg.enabled}|{langs}"
        if source.kind in ("audio", "video"):
            tc = self.config.transcribe
            return f"{tc.backend}|{tc.parakeet_model}|{tc.whisper_model}|p{tc.paragraph_seconds}"
        return "v1"

    async def _process(self, source: Source) -> tuple[ProcessedSource, str]:
        variant = self._variant(source)
        key = cache.cache_key(source.sha256, source.kind, source.language, variant)
        entry = await asyncio.to_thread(cache.lookup, self.db, key)
        if entry is not None:
            log.info("cache_hit", extra={"source_id": source.id})
            doc = cache.load_processed(
                entry.processed_path,
                source.id,
                source.title,
                self.config.llm.chars_per_token,
            )
            doc.meta = {**doc.meta, "cache_hit": True}
            return doc, variant
        path = Path(source.stored_path)
        if not await asyncio.to_thread(path.exists):
            raise FileNotFoundError(f"{source.original_name} is no longer at {path}")
        if source.kind == "text":
            return await self._process_text(source, path), variant
        if source.kind == "docx":
            return await self._process_docx(source, path), variant
        if source.kind == "subtitles":
            return await self._process_subtitles(source, path), variant
        if source.kind == "pdf":
            return await self._process_pdf(source, path), variant
        if source.kind in ("audio", "video"):
            return await self._process_media(source, path), variant
        raise ValueError(f"Unsupported kind {source.kind}")

    def _doc(self, source: Source, markdown: str, meta: dict[str, Any]) -> ProcessedSource:
        meta = {**meta, "sha256": source.sha256}
        return ProcessedSource(
            source_id=source.id,
            title=source.title,
            kind=source.kind,
            meta=meta,
            text_markdown=markdown,
            token_estimate=estimate_tokens(markdown, self.config.llm.chars_per_token),
        )

    async def _process_text(self, source: Source, path: Path) -> ProcessedSource:
        await self._stage(source, "extracting", "cpu", 0.2)
        content = await self.resources.run_cpu(text.read_text_file, path)
        return self._doc(source, content, {"chars": len(content)})

    async def _process_docx(self, source: Source, path: Path) -> ProcessedSource:
        await self._stage(source, "extracting", "cpu", 0.2)
        content = await self.resources.run_cpu(docx.docx_to_markdown, path)
        return self._doc(source, content, {"chars": len(content)})

    async def _process_subtitles(self, source: Source, path: Path) -> ProcessedSource:
        await self._stage(source, "extracting", "cpu", 0.2)
        segments = await self.resources.run_cpu(subtitles.read_subtitles, path)
        markdown = transcript_markdown(segments, self.config.transcribe.paragraph_seconds)
        duration = max((s.end for s in segments), default=0.0)
        return self._doc(
            source, markdown, {"duration_s": round(duration, 1), "segments": len(segments)}
        )

    async def _process_pdf(self, source: Source, path: Path) -> ProcessedSource:
        await self._stage(source, "extracting", "cpu", 0.1)
        pages = await self.resources.run_cpu(pdf.extract_pages, path, self.config.pdf.extractor)
        meta: dict[str, Any] = {"pages": len(pages), "extractor": self.config.pdf.extractor}
        ocr_cfg = self.config.ocr
        if pdf.needs_ocr(pages, ocr_cfg.min_chars_per_page, ocr_cfg.empty_page_ratio):
            if not ocr_cfg.enabled:
                raise RuntimeError(
                    "PDF has little or no extractable text and OCR is disabled (ocr.enabled)."
                )
            if not ocr.ocr_available():
                raise RuntimeError(
                    "PDF looks scanned; OCR needs macOS with `uv sync --extra mac` (ocrmac)."
                )
            await self._stage(source, "rendering", "cpu", 0.2)
            count = await self.resources.run_cpu(pdf.page_count, path)
            await self._stage(source, "ocr", "cpu", 0.25)
            languages = ocr_cfg.languages
            base = base_language(source.language)
            if base is not None:
                languages = [_ocr_locale(base)] + [
                    lang for lang in languages if not lang.lower().startswith(base)
                ]
            report = self._progress_reporter(source, "ocr", 0.25, 0.95)
            tasks = [
                self.resources.run_cpu(ocr.ocr_pdf_page, path, i, ocr_cfg.dpi, languages)
                for i in range(count)
            ]
            pages = []
            for done, coro in enumerate(asyncio.as_completed(tasks), start=1):
                pages.append(await coro)
                report(done / max(1, count))
            pages.sort(key=lambda p: p.number)
            meta.update({"ocr": True, "ocr_languages": languages})
        return self._doc(source, pdf.pages_markdown(pages), meta)

    async def _process_media(self, source: Source, path: Path) -> ProcessedSource:
        ffmpeg = find_ffmpeg(self.config.transcribe.ffmpeg_path)
        if ffmpeg is None:
            raise RuntimeError(
                "ffmpeg not found; install it (`brew install ffmpeg`) or set "
                "transcribe.ffmpeg_path."
            )
        wav = self.config.media_dir / f"{source.sha256}.wav"
        await self._stage(source, "transcoding", "subprocess", 0.05)
        if not await asyncio.to_thread(wav.exists):
            async with self.resources.subprocess:
                await media.extract_wav(ffmpeg, path, wav)
        duration = await asyncio.to_thread(media.wav_duration_seconds, wav)
        # Keep the code as chosen ("zh-TW"), not just its base: backends that distinguish
        # regional variants need it; the others normalize it themselves.
        language: str | None = source.language if base_language(source.language) else None
        detected: str | None = None
        if language is None:
            detector = self.transcribers.detector()
            if detector is not None:
                await self._stage(source, "detecting_language", "gpu", 0.1)
                async with self.resources.gpu:
                    detected = await self.resources.run_blocking(
                        detector.detect_language, wav, self.config.transcribe.detection_seconds
                    )
                language = detected if base_language(detected) else None
        transcriber = self.transcribers.route(language)
        await self._stage(source, "transcribing", "gpu", 0.15)
        report = self._progress_reporter(source, "transcribing", 0.15, 0.95)
        async with self.resources.gpu:
            transcript: Transcript = await self.resources.run_blocking(
                transcriber.transcribe, wav, language=language, progress=report
            )
        segments = transcript.segments
        if language is not None:
            segments = [
                seg.model_copy(update={"text": normalize_script(seg.text, language)})
                for seg in segments
            ]
        markdown = transcript_markdown(segments, self.config.transcribe.paragraph_seconds)
        meta = {
            "duration_s": round(duration, 1),
            "segments": len(transcript.segments),
            "detected_language": transcript.language or detected or language,
            "transcriber": transcriber.name,
            "transcriber_model": transcript.model,
        }
        return self._doc(source, markdown, meta)

    # -- messages --------------------------------------------------------------------------

    async def submit_message(
        self, session_id: str, content: str, run_with_ready_only: bool, full_text: bool = False
    ) -> Message:
        """Queue an instruction. ``@name rest`` runs the skill ``name`` with ``rest`` as its
        arguments; ``@@`` at the start sends a literal at sign."""
        self._check_open()
        content = content.strip()
        if not content:
            raise ValueError("Instruction must not be empty")
        await asyncio.to_thread(self.db.get_session, session_id)
        command = parse_command(content)
        use = prompt = None
        if command is None:
            content = unescape(content)
        else:
            try:
                skill = await asyncio.to_thread(self.skills.load, command.name)
            except SkillNotFoundError:
                names = await asyncio.to_thread(self.skills.names)
                listed = ", ".join(f"@{n}" for n in names) or "none yet"
                raise ValueError(
                    f"There is no skill named @{command.name}. Skills: {listed}. To send text "
                    "that starts with @, begin it with @@."
                ) from None
            use, prompt = render(skill, command.arguments)
            full_text = full_text or skill.full_text
            log.info("skill_run", extra={"skill": skill.name, "session_id": session_id})
        now = utc_now()
        user = Message(
            id=new_id(),
            session_id=session_id,
            role="user",
            content=content,
            status="done",
            skill=use,
            prompt=prompt,
            created_at=now,
            updated_at=now,
        )
        assistant = Message(
            id=new_id(),
            session_id=session_id,
            role="assistant",
            status="pending",
            run_with_ready_only=run_with_ready_only,
            full_text=full_text,
            skill=use,
            created_at=now,
            updated_at=now,
        )
        await asyncio.to_thread(self.db.create_message, user)
        await asyncio.to_thread(self.db.create_message, assistant)
        self.events.publish(Event("message.updated", user.model_dump(), session_id=session_id))
        self.events.publish(Event("message.updated", assistant.model_dump(), session_id=session_id))
        await self._start_message_if_ready(assistant)
        return assistant

    async def cancel_message(self, message_id: str) -> Message:
        task = self._message_tasks.get(message_id)
        if task is not None and not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        message = await asyncio.to_thread(self.db.get_message, message_id)
        if message.status in ("pending", "waiting_for_sources", "streaming"):
            message = await self._set_message(message_id, status="cancelled")
        return message

    async def _set_message(self, message_id: str, **fields: Any) -> Message:
        message = await asyncio.to_thread(self.db.update_message, message_id, **fields)
        self.events.publish(
            Event("message.updated", message.model_dump(), session_id=message.session_id)
        )
        return message

    async def _maybe_start_waiting_messages(self, session_id: str) -> None:
        if self._halted:
            return
        try:
            waiting = await asyncio.to_thread(self.db.waiting_messages, session_id)
        except NotFoundError:
            return
        for message in waiting:
            await self._start_message_if_ready(message)

    async def _start_message_if_ready(self, message: Message) -> None:
        if self._halted:
            return
        sources = await asyncio.to_thread(self.db.list_sources, message.session_id)
        pending = [s for s in sources if s.status not in TERMINAL_SOURCE_STATUSES]
        if pending and not message.run_with_ready_only:
            if message.status != "waiting_for_sources":
                await self._set_message(message.id, status="waiting_for_sources")
            return
        if message.id in self._message_tasks and not self._message_tasks[message.id].done():
            return
        task = asyncio.create_task(self._run_message(message.id), name=f"message:{message.id}")
        self._message_tasks[message.id] = task
        task.add_done_callback(lambda t: self._message_tasks.pop(message.id, None))

    async def _run_message(self, message_id: str) -> None:
        message = await asyncio.to_thread(self.db.get_message, message_id)
        session_id = message.session_id
        try:
            history_all = await asyncio.to_thread(self.db.list_messages, session_id)
            # The user message is created with the same timestamp as its answer.
            asked = next(
                (
                    m
                    for m in reversed(history_all)
                    if m.role == "user" and m.created_at <= message.created_at
                ),
                None,
            )
            instruction = "" if asked is None else (asked.prompt or asked.content)
            skill = None if asked is None else asked.skill
            history = [m for m in history_all if m.created_at < message.created_at]
            sources = await asyncio.to_thread(self.db.list_sources, session_id)
            docs, notes = await self._load_docs(sources)
            languages = [s.language for s in sources if s.status == "ready" and s.processed_path]
            await self._set_message(message_id, status="streaming", content="")
            reading = self._prepare_tasks.get(session_id)
            if reading is not None and not reading.done():
                self.events.publish(
                    Event(
                        "message.progress",
                        {"message_id": message_id, "detail": "Finishing reading the sources"},
                        session_id=session_id,
                    )
                )
                await asyncio.wait({reading}, timeout=PREPARE_WAIT_S)

            async def emit(delta: str) -> None:
                await asyncio.to_thread(self.db.append_message_content, message_id, delta)
                self.events.publish(
                    Event(
                        "message.delta",
                        {"message_id": message_id, "text": delta},
                        session_id=session_id,
                    )
                )

            async def progress(detail: str) -> None:
                self.events.publish(
                    Event(
                        "message.progress",
                        {"message_id": message_id, "detail": detail},
                        session_id=session_id,
                    )
                )

            result = await self.synthesizer.run(
                instruction=instruction,
                sources=docs,
                notes=notes,
                history=history,
                emit=emit,
                progress=progress,
                full_text=message.full_text,
                source_languages=languages,
                skill=skill,
            )
            final = await asyncio.to_thread(self.db.get_message, message_id)
            await self._set_message(
                message_id, status="done", strategy=result.strategy, token_usage=result.usage
            )
            named_after = instruction if skill is None else f"{skill.title}\n{skill.body}"
            self._spawn(
                self._after_answer(
                    session_id, history_all, named_after, final.content, result.script
                )
            )
        except asyncio.CancelledError:
            await self._set_message(message_id, status="cancelled")
            raise
        except Exception as exc:
            log.exception("message_failed", extra={"message_id": message_id})
            await self._set_message(
                message_id, status="failed", error=f"{type(exc).__name__}: {exc}"[:1000]
            )

    async def _after_answer(
        self,
        session_id: str,
        history: list[Message],
        instruction: str,
        answer: str,
        script_target: str | None,
    ) -> None:
        await self._maybe_name_session(session_id, history, instruction, answer, script_target)
        # The next question's prefix now includes this turn; read it while the user reads.
        self.schedule_prepare(session_id)

    async def _maybe_name_session(
        self,
        session_id: str,
        history: list[Message],
        instruction: str,
        answer: str,
        script_target: str | None = None,
    ) -> None:
        """Name the session after its first completed answer, unless the user named it."""
        first_answer = not any(m.role == "assistant" and m.status == "done" for m in history)
        if not first_answer:
            return
        try:
            session = await asyncio.to_thread(self.db.get_session, session_id)
        except NotFoundError:
            return
        if not session.title_auto:
            return
        title = await self.synthesizer.suggest_title(instruction, answer, script_target)
        if not title:
            return
        session = await asyncio.to_thread(self.db.update_session, session_id, title=title)
        self.events.publish(Event("session.updated", session.model_dump(), session_id=session_id))


def _notes_meta(notes: SourceNotes) -> dict[str, Any]:
    return {
        "status": "done",
        "sections": len(notes.sections),
        "tokens": notes.token_estimate,
        "model": notes.model,
    }


def _ocr_locale(base: str) -> str:
    from raida.transcribe.languages import APPLE_LOCALES

    return APPLE_LOCALES.get(base, base)

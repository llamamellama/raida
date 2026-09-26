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
from raida.llm.registry import build_llm_backend
from raida.llm.synthesis import Synthesizer
from raida.llm.tokens import estimate_tokens
from raida.models import (
    TERMINAL_SOURCE_STATUSES,
    HealthReport,
    Message,
    ProcessedSource,
    SessionDetail,
    Source,
    new_id,
    utc_now,
)
from raida.pipeline import cache
from raida.pipeline.events import Event, EventBus
from raida.pipeline.ingest import remove_stored_file_if_unreferenced
from raida.pipeline.resources import ResourcePool
from raida.pipeline.stages import docx, media, ocr, pdf, subtitles, text
from raida.transcribe.base import Transcript
from raida.transcribe.format import transcript_markdown
from raida.transcribe.languages import base_language
from raida.transcribe.registry import TranscriberRegistry

log = logging.getLogger(__name__)


class Scheduler:
    def __init__(self, config: Config, db: Database) -> None:
        self.config = config
        self.db = db
        self.events = EventBus()
        self.resources = ResourcePool(config)
        self.transcribers = TranscriberRegistry(config)
        self.llm = build_llm_backend(config)
        self.synthesizer = Synthesizer(config, db, self.llm, self.resources.llm)
        self._source_tasks: dict[str, asyncio.Task[None]] = {}
        self._message_tasks: dict[str, asyncio.Task[None]] = {}
        self._background: set[asyncio.Task[Any]] = set()
        self._loop: asyncio.AbstractEventLoop | None = None
        self.last_health: HealthReport | None = None

    # -- lifecycle -------------------------------------------------------------------------

    async def start(self) -> None:
        self._loop = asyncio.get_running_loop()
        self.resources.start()
        for source in await asyncio.to_thread(self.db.unfinished_sources):
            log.info("requeue_source", extra={"source_id": source.id, "status": source.status})
            await self._set_source(source.id, status="queued", progress=0.0, error=None)
            self.submit_source(source.id)
        self._spawn(self._warm_up_llm())

    async def stop(self) -> None:
        tasks = [*self._source_tasks.values(), *self._message_tasks.values(), *self._background]
        for task in tasks:
            task.cancel()
        for task in tasks:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        self.resources.shutdown()
        await self.llm.aclose()

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

    # -- source control --------------------------------------------------------------------

    def submit_source(self, source_id: str) -> None:
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
        await self.cancel_source(source_id)
        source = await self._set_source(
            source_id, status="queued", progress=0.0, error=None, processed_path=None
        )
        self.submit_source(source_id)
        return source

    async def set_language(self, source_id: str, language: str) -> Source:
        source = await asyncio.to_thread(self.db.get_source, source_id)
        if source.language == language:
            return source
        source = await self._set_source(source_id, language=language)
        if source.kind in ("audio", "video", "pdf"):
            return await self.retry_source(source_id)
        return source

    async def remove_source(self, source_id: str) -> None:
        source = await self.cancel_source(source_id)
        await asyncio.to_thread(self.db.delete_source, source_id)
        await asyncio.to_thread(remove_stored_file_if_unreferenced, self.db, source)
        self.events.publish(
            Event("source.removed", {"source_id": source_id}, session_id=source.session_id)
        )
        await self._maybe_start_waiting_messages(source.session_id)

    async def _set_source(self, source_id: str, **fields: Any) -> Source:
        source = await asyncio.to_thread(self.db.update_source, source_id, **fields)
        self.events.publish(
            Event("source.updated", source.model_dump(), session_id=source.session_id)
        )
        return source

    def _progress_reporter(
        self, source: Source, stage: str, lo: float, hi: float
    ) -> Callable[[float], None]:
        loop = self._loop
        session_id = source.session_id

        def report(fraction: float) -> None:
            fraction = max(0.0, min(1.0, fraction))
            value = lo + (hi - lo) * fraction
            event = Event(
                "job.progress",
                {"source_id": source.id, "stage": stage, "progress": round(value, 3)},
                session_id=session_id,
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
            await self._set_source(
                source.id,
                status="ready",
                progress=1.0,
                error=None,
                processed_path=str(md_path),
                token_estimate=doc.token_estimate,
                meta=doc.meta,
            )
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
            await self._maybe_start_waiting_messages(source.session_id)

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
            doc = cache.load_processed(entry.processed_path, source.id, source.original_name)
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
            title=source.original_name,
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
        language = base_language(source.language)
        detected: str | None = None
        if language is None:
            detector = self.transcribers.detector()
            if detector is not None:
                await self._stage(source, "detecting_language", "gpu", 0.1)
                async with self.resources.gpu:
                    detected = await self.resources.run_blocking(
                        detector.detect_language, wav, self.config.transcribe.detection_seconds
                    )
                language = base_language(detected)
        transcriber = self.transcribers.route(language)
        await self._stage(source, "transcribing", "gpu", 0.15)
        report = self._progress_reporter(source, "transcribing", 0.15, 0.95)
        async with self.resources.gpu:
            transcript: Transcript = await self.resources.run_blocking(
                transcriber.transcribe, wav, language=language, progress=report
            )
        markdown = transcript_markdown(
            transcript.segments, self.config.transcribe.paragraph_seconds
        )
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
        self, session_id: str, content: str, run_with_ready_only: bool
    ) -> Message:
        content = content.strip()
        if not content:
            raise ValueError("Instruction must not be empty")
        await asyncio.to_thread(self.db.get_session, session_id)
        now = utc_now()
        user = Message(
            id=new_id(),
            session_id=session_id,
            role="user",
            content=content,
            status="done",
            created_at=now,
            updated_at=now,
        )
        assistant = Message(
            id=new_id(),
            session_id=session_id,
            role="assistant",
            status="pending",
            run_with_ready_only=run_with_ready_only,
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
        try:
            waiting = await asyncio.to_thread(self.db.waiting_messages, session_id)
        except NotFoundError:
            return
        for message in waiting:
            await self._start_message_if_ready(message)

    async def _start_message_if_ready(self, message: Message) -> None:
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
            instruction = next(
                (
                    m.content
                    for m in reversed(history_all)
                    if m.role == "user" and m.created_at <= message.created_at
                ),
                "",
            )
            history = [
                m
                for m in history_all
                if m.created_at < message.created_at and m.content != instruction
            ]
            sources = await asyncio.to_thread(self.db.list_sources, session_id)
            ready = [s for s in sources if s.status == "ready" and s.processed_path]
            docs = [cache.load_processed(s.processed_path, s.id, s.original_name) for s in ready]  # type: ignore[arg-type]
            await self._set_message(message_id, status="streaming", content="")

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
                instruction=instruction, sources=docs, history=history, emit=emit, progress=progress
            )
            final = await asyncio.to_thread(self.db.get_message, message_id)
            await self._set_message(
                message_id, status="done", strategy=result.strategy, token_usage=result.usage
            )
            title = await self.synthesizer.suggest_title(instruction, final.content)
            if title:
                session = await asyncio.to_thread(self.db.update_session, session_id, title=title)
                self.events.publish(
                    Event("session.updated", session.model_dump(), session_id=session_id)
                )
        except asyncio.CancelledError:
            await self._set_message(message_id, status="cancelled")
            raise
        except Exception as exc:
            log.exception("message_failed", extra={"message_id": message_id})
            await self._set_message(
                message_id, status="failed", error=f"{type(exc).__name__}: {exc}"[:1000]
            )


def _ocr_locale(base: str) -> str:
    from raida.transcribe.languages import APPLE_LOCALES

    return APPLE_LOCALES.get(base, base)

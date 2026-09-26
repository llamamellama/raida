"""Notes taken on each long source as soon as its text is ready.

Reading a prompt costs time that grows faster than its length, so answering from the full text
of several long recordings makes every first question wait many minutes. Notes move that work
to the moment a file is added: each section of about 25 minutes of speech is turned into
anchored notes of about a fifth of its length, then an overview is written from them. Notes
depend only on the file and the model, never on a question, so every session that includes
the file reuses them at no cost. Calls run as background work (see ``gate.py``) with reasoning
off; each section is cached as it finishes, so an interrupted run resumes where it stopped.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
from collections.abc import Callable

from raida.config import Config
from raida.db import Database
from raida.llm import prompts
from raida.llm.base import GenerationOptions, LlmBackend
from raida.llm.chunking import split_markdown
from raida.llm.gate import LlmGate
from raida.llm.tokens import estimate_tokens
from raida.models import NoteSection, ProcessedSource, SourceNotes, utc_now
from raida.script import han_script, normalize_script, profiles_for

log = logging.getLogger(__name__)

# Bump when a prompt or format change makes stored notes worth taking again.
NOTES_VERSION = "n1"
OVERVIEW_RATIO = 0.25  # of the notes, bounded below
MAX_NOTE_TOKENS = 4096

_ANCHOR = re.compile(r"\[(?:\d{2}:\d{2}:\d{2}|p\. \d+)\]")
_THINK_BLOCK = re.compile(r"^\s*<think>.*?</think>\s*", re.DOTALL)

Progress = Callable[[int, int], None]  # sections done, sections total


def notes_key(processed_key: str, model: str) -> str:
    raw = "|".join((NOTES_VERSION, processed_key, model))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _clean(text: str) -> str:
    # A model that reasons despite the request sometimes leaves its reasoning block in front.
    return _THINK_BLOCK.sub("", text).strip()


class NoteTaker:
    def __init__(self, config: Config, db: Database, llm: LlmBackend, gate: LlmGate) -> None:
        self.config = config
        self.db = db
        self.llm = llm
        self.gate = gate
        self._locks: dict[str, asyncio.Lock] = {}

    def needed(self, doc: ProcessedSource) -> bool:
        cfg = self.config.notes
        return cfg.enabled and doc.token_estimate >= cfg.min_source_tokens

    def key(self, processed_key: str) -> str:
        return notes_key(processed_key, self.llm.model)

    async def load(self, processed_key: str) -> SourceNotes | None:
        return await asyncio.to_thread(self.db.get_notes, self.key(processed_key))

    async def ensure(
        self,
        processed_key: str,
        doc: ProcessedSource,
        language: str | None,
        progress: Progress | None = None,
    ) -> SourceNotes:
        """Stored notes for the document, taking them first if there are none. Concurrent
        callers for the same document wait for one run instead of starting their own."""
        key = self.key(processed_key)
        lock = self._locks.setdefault(key, asyncio.Lock())
        async with lock:
            existing = await asyncio.to_thread(self.db.get_notes, key)
            if existing is not None:
                return existing
            notes = await self._take(key, doc, language, progress)
            await asyncio.to_thread(self.db.put_notes, notes)
            return notes

    def _options(self, max_tokens: int) -> GenerationOptions:
        return GenerationOptions(
            num_ctx=self.config.llm.num_ctx,
            max_tokens=max_tokens,
            temperature=self.config.llm.temperature,
            think=False,
        )

    async def _call(
        self, key: str, stage: str, payload: str, system: str, user: str, max_tokens: int
    ) -> str:
        """One background model call, cached by its input so a restarted run resumes."""
        call_key = hashlib.sha256(f"{key}|{stage}|{payload}".encode()).hexdigest()
        cached = await asyncio.to_thread(self.db.get_condensation, call_key)
        if cached is not None:
            return cached
        messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        options = self._options(max_tokens)
        text = _clean(await self.gate.background(lambda: self.llm.complete(messages, options)))
        if not text:
            raise RuntimeError(f"the model returned no {stage} text")
        await asyncio.to_thread(
            self.db.put_condensation,
            call_key,
            sha256=key,
            instruction_hash=stage,
            model=self.llm.model,
            text=text,
        )
        return text

    async def _take(
        self, key: str, doc: ProcessedSource, language: str | None, progress: Progress | None
    ) -> SourceNotes:
        if not profiles_for(language):
            # No regional code (a document, or "auto"): hold Chinese notes to the script the
            # source is written in, which the model does not always keep.
            language = {"hant": "zh-Hant", "hans": "zh-CN"}.get(han_script(doc.text_markdown) or "")
        cfg = self.config.notes
        cpt = self.config.llm.chars_per_token
        chunks = split_markdown(doc.text_markdown, cfg.section_tokens, cpt, overlap_ratio=0.0)
        total = len(chunks) + 1  # plus the overview
        sections: list[NoteSection] = []
        log.info("notes_start", extra={"source_id": doc.source_id, "sections": len(chunks)})
        for index, chunk in enumerate(chunks, start=1):
            if progress:
                progress(index - 1, total)
            budget = int(estimate_tokens(chunk, cpt) * cfg.ratio * 2.5) + 200
            text = await self._call(
                key,
                f"section-{index}",
                chunk,
                prompts.NOTES_SYSTEM,
                prompts.notes_user_message(
                    doc, chunk, index, len(chunks), prompts.length_hint(chunk, cfg.ratio)
                ),
                min(MAX_NOTE_TOKENS, budget),
            )
            anchors = _ANCHOR.findall(chunk)
            sections.append(
                NoteSection(
                    start=anchors[0] if anchors else "",
                    end=anchors[-1] if anchors else "",
                    text=normalize_script(text, language),
                )
            )
        if progress:
            progress(len(chunks), total)
        joined = "\n\n".join(s.text for s in sections)
        overview = await self._call(
            key,
            "overview",
            joined,
            prompts.OVERVIEW_SYSTEM,
            prompts.overview_user_message(
                doc, joined, prompts.length_hint(joined, OVERVIEW_RATIO / max(1, len(chunks)))
            ),
            1024,
        )
        notes = SourceNotes(
            key=key,
            sha256=str(doc.meta.get("sha256", "")),
            model=self.llm.model,
            overview=normalize_script(overview, language),
            sections=sections,
            token_estimate=0,
            created_at=utc_now(),
        )
        notes.token_estimate = estimate_tokens(notes.markdown(), cpt)
        if progress:
            progress(total, total)
        log.info(
            "notes_done",
            extra={
                "source_id": doc.source_id,
                "sections": len(sections),
                "tokens": notes.token_estimate,
                "source_tokens": doc.token_estimate,
            },
        )
        return notes

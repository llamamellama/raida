"""Single-shot or map-reduce synthesis over processed sources, streaming the final answer."""

from __future__ import annotations

import asyncio
import hashlib
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from raida.config import Config
from raida.db import Database
from raida.llm import prompts
from raida.llm.base import GenerationOptions, LlmBackend, Usage
from raida.llm.chunking import split_markdown
from raida.llm.tokens import estimate_tokens
from raida.models import Message, ProcessedSource, Strategy

log = logging.getLogger(__name__)

Emit = Callable[[str], Awaitable[None]]
Progress = Callable[[str], Awaitable[None]]


@dataclass
class SynthesisResult:
    strategy: Strategy
    usage: dict[str, Any] = field(default_factory=dict)


class Synthesizer:
    def __init__(
        self, config: Config, db: Database, llm: LlmBackend, llm_slot: asyncio.Semaphore
    ) -> None:
        self.config = config
        self.db = db
        self.llm = llm
        self.llm_slot = llm_slot

    # -- budgeting -------------------------------------------------------------------------

    def _tokens(self, text: str) -> int:
        return estimate_tokens(text, self.config.llm.chars_per_token)

    def _options(self, max_tokens: int | None = None) -> GenerationOptions:
        cfg = self.config.llm
        return GenerationOptions(
            num_ctx=cfg.num_ctx,
            max_tokens=max_tokens or cfg.output_reserve_tokens,
            temperature=cfg.temperature,
        )

    def _history_messages(self, history: list[Message]) -> list[dict[str, str]]:
        """Most recent completed turns that fit the history budget, oldest first."""
        budget = self.config.llm.history_budget_tokens
        selected: list[dict[str, str]] = []
        used = 0
        for msg in reversed(history):
            if msg.status not in ("done",) and msg.role == "assistant":
                continue
            if not msg.content.strip():
                continue
            cost = self._tokens(msg.content)
            if used + cost > budget:
                break
            selected.append({"role": msg.role, "content": msg.content})
            used += cost
        selected.reverse()
        return selected

    def plan(
        self, sources: list[ProcessedSource], instruction: str, history: list[Message]
    ) -> Strategy:
        fixed = self._tokens(prompts.SYSTEM_PROMPT) + self._tokens(instruction)
        fixed += sum(self._tokens(m["content"]) for m in self._history_messages(history))
        total = fixed + sum(s.token_estimate for s in sources)
        return "single_shot" if total <= self.config.llm.synthesis_budget_tokens else "map_reduce"

    # -- public entry ----------------------------------------------------------------------

    async def run(
        self,
        *,
        instruction: str,
        sources: list[ProcessedSource],
        history: list[Message],
        emit: Emit,
        progress: Progress,
    ) -> SynthesisResult:
        strategy = self.plan(sources, instruction, history)
        log.info("synthesis_plan", extra={"strategy": strategy, "sources": len(sources)})
        usage = Usage()
        if strategy == "map_reduce":
            await progress("Condensing long sources before writing")
            sources = await self._condense_sources(instruction, sources, progress)
        messages = [{"role": "system", "content": prompts.SYSTEM_PROMPT}]
        if sources:
            messages.append(
                {
                    "role": "user",
                    "content": prompts.sources_message(sources, condensed=strategy == "map_reduce"),
                }
            )
            messages.append(
                {
                    "role": "assistant",
                    "content": "I have read the sources and will work only from them.",
                }
            )
        messages.extend(self._history_messages(history))
        messages.append({"role": "user", "content": instruction})
        await progress("Writing")
        async with self.llm_slot:
            async for delta in self.llm.stream_chat(messages, self._options(), usage):
                await emit(delta)
        return SynthesisResult(
            strategy=strategy,
            usage={
                "prompt_tokens": usage.prompt_tokens,
                "completion_tokens": usage.completion_tokens,
                **usage.extra,
            },
        )

    async def suggest_title(self, instruction: str, answer: str) -> str | None:
        if not self.config.llm.suggest_titles:
            return None
        messages = [
            {"role": "system", "content": "Return a short title for the document below."},
            {
                "role": "user",
                "content": f"Instruction: {instruction[:500]}\n\nDocument:\n{answer[:4000]}",
            },
        ]
        try:
            async with self.llm_slot:
                result = await self.llm.complete_json(
                    messages, prompts.TITLE_SCHEMA, self._options(64)
                )
        except Exception as exc:
            log.warning("title_suggestion_failed", extra={"error": str(exc)[:200]})
            return None
        title = str(result.get("title", "")).strip()
        return title[:120] or None

    # -- map-reduce ------------------------------------------------------------------------

    def _per_source_budget(self, count: int) -> int:
        cfg = self.config.llm
        available = cfg.synthesis_budget_tokens - self._tokens(prompts.SYSTEM_PROMPT) - 1500
        return max(cfg.condensation_target_tokens, available // max(1, count))

    async def _condense_sources(
        self, instruction: str, sources: list[ProcessedSource], progress: Progress
    ) -> list[ProcessedSource]:
        per_source = self._per_source_budget(len(sources))
        out: list[ProcessedSource] = []
        for source in sources:
            if source.token_estimate <= per_source:
                out.append(source)
                continue
            await progress(f"Condensing {source.title}")
            text = await self._condense_one(instruction, source, per_source, progress)
            out.append(
                source.model_copy(
                    update={
                        "text_markdown": text,
                        "token_estimate": self._tokens(text),
                        "meta": {**source.meta, "condensed": True},
                    }
                )
            )
        return out

    def _cache_key(
        self, source: ProcessedSource, instruction: str, stage: str, payload: str
    ) -> str:
        sha = str(source.meta.get("sha256", source.source_id))
        instruction_hash = hashlib.sha256(instruction.strip().encode("utf-8")).hexdigest()[:16]
        raw = "|".join(
            (
                sha,
                instruction_hash,
                self.llm.model,
                stage,
                hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16],
            )
        )
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    async def _cached_call(
        self,
        source: ProcessedSource,
        instruction: str,
        stage: str,
        payload: str,
        system: str,
        user: str,
        max_tokens: int,
    ) -> str:
        key = self._cache_key(source, instruction, stage, payload)
        cached = await asyncio.to_thread(self.db.get_condensation, key)
        if cached is not None:
            return cached
        messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        async with self.llm_slot:
            text = await self.llm.complete(messages, self._options(max_tokens))
        instruction_hash = hashlib.sha256(instruction.strip().encode("utf-8")).hexdigest()[:16]
        await asyncio.to_thread(
            self.db.put_condensation,
            key,
            sha256=str(source.meta.get("sha256", source.source_id)),
            instruction_hash=instruction_hash,
            model=self.llm.model,
            text=text,
        )
        return text

    async def _condense_one(
        self, instruction: str, source: ProcessedSource, budget: int, progress: Progress
    ) -> str:
        cfg = self.config.llm
        chunks = split_markdown(source.text_markdown, cfg.map_chunk_tokens, cfg.chars_per_token)
        target_tokens = max(300, min(cfg.condensation_target_tokens, budget // max(1, len(chunks))))
        target_words = int(target_tokens * 0.75)
        notes: list[str] = []
        for index, chunk in enumerate(chunks, start=1):
            await progress(f"Condensing {source.title} ({index}/{len(chunks)})")
            note = await self._cached_call(
                source,
                instruction,
                "map",
                chunk,
                prompts.CONDENSE_SYSTEM,
                prompts.condense_user_message(
                    instruction, source, chunk, target_words, index, len(chunks)
                ),
                max_tokens=target_tokens + 200,
            )
            notes.append(note)
        merged = "\n\n".join(notes)
        # Hierarchical reduce: merge groups of notes until the source fits its share.
        while self._tokens(merged) > budget and len(notes) > 1:
            await progress(f"Merging notes for {source.title}")
            group_size = 4
            grouped = [notes[i : i + group_size] for i in range(0, len(notes), group_size)]
            target_words = int(budget * 0.75 / max(1, len(grouped)))
            notes = [
                await self._cached_call(
                    source,
                    instruction,
                    "reduce",
                    "\n".join(group),
                    prompts.MERGE_SYSTEM,
                    prompts.merge_user_message(instruction, source.title, group, target_words),
                    max_tokens=int(target_words * 1.4) + 200,
                )
                for group in grouped
            ]
            merged = "\n\n".join(notes)
        return merged

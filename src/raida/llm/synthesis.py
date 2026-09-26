"""Answers over processed sources, streaming the final text.

Two paths:

- Fast (default). Short sources go in full; long sources go in as the notes taken when they
  were added (``notes.py``), and verbatim passages found for the question are appended to it.
  The prompt starts with a prefix that does not depend on the question (system prompt,
  sources, earlier turns), so a server with a prompt cache reads it once per session, and it
  can be read before the question is asked (``prefill``).
- Full text (``full_text=True``). Every source verbatim when it fits
  ``llm.synthesis_budget_tokens``, otherwise condensed for the instruction first (map-reduce).
  Slower, for questions that need every detail.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from raida.config import Config
from raida.db import Database
from raida.llm import prompts, retrieval
from raida.llm.base import ChatMessage, GenerationOptions, LlmBackend, LlmError, Usage
from raida.llm.chunking import split_markdown
from raida.llm.gate import LlmGate
from raida.llm.tokens import estimate_tokens
from raida.models import Message, ProcessedSource, SourceNotes, Strategy
from raida.script import AnswerScript, ScriptStream, answer_script, convert_to

log = logging.getLogger(__name__)

Emit = Callable[[str], Awaitable[None]]
Progress = Callable[[str], Awaitable[None]]

ACK = "I have read the sources and will work only from them."
PREPARED_MEMORY = 32  # prefixes remembered as read ahead, for the progress message
# Room kept for the instruction in the fast-path plan, so the choice of note forms (and with it
# the cached prompt prefix) does not change from one question to the next.
INSTRUCTION_ALLOWANCE = 1_000
REASONING_REPORT_S = 1.0
SCRIPT_SAMPLE_CHARS = 1_500  # per source, to tell the script of the sources


@dataclass
class SynthesisResult:
    strategy: Strategy
    usage: dict[str, Any] = field(default_factory=dict)
    script: str | None = None  # Chinese script the answer was held to, for the session title


@dataclass
class FastPlan:
    strategy: Strategy
    prefix: list[ChatMessage]
    # Sources given through notes or an overview, whose full text is searched for passages.
    searchable: list[tuple[int, ProcessedSource]]
    forms: dict[str, str]  # source_id -> "full" | "notes" | "overview"

    def prefix_hash(self) -> str:
        raw = "\x1e".join(f"{m['role']}\x1f{m['content']}" for m in self.prefix)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class Synthesizer:
    def __init__(self, config: Config, db: Database, llm: LlmBackend, gate: LlmGate) -> None:
        self.config = config
        self.db = db
        self.llm = llm
        self.gate = gate
        self._prepared: dict[str, None] = {}  # prefix hashes read ahead, oldest first

    # -- budgeting -------------------------------------------------------------------------

    def _tokens(self, text: str) -> int:
        return estimate_tokens(text, self.config.llm.chars_per_token)

    def _options(
        self, max_tokens: int | None = None, think: bool | None = None
    ) -> GenerationOptions:
        cfg = self.config.llm
        return GenerationOptions(
            num_ctx=cfg.num_ctx,
            max_tokens=max_tokens or cfg.output_reserve_tokens,
            temperature=cfg.temperature,
            think=think,
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
        """Strategy of the full-text path."""
        fixed = self._tokens(prompts.SYSTEM_PROMPT) + self._tokens(instruction)
        fixed += sum(self._tokens(m["content"]) for m in self._history_messages(history))
        total = fixed + sum(s.token_estimate for s in sources)
        return "single_shot" if total <= self.config.llm.synthesis_budget_tokens else "map_reduce"

    # -- fast path -------------------------------------------------------------------------

    def plan_fast(
        self,
        sources: list[ProcessedSource],
        notes: dict[str, SourceNotes],
        history: list[Message],
        instruction: str = "",
    ) -> FastPlan | None:
        """Prompt prefix for the fast path, or None when it does not apply: a long source has
        no notes, or even overviews of every source exceed the synthesis budget."""
        cfg = self.config
        history_msgs = self._history_messages(history)
        fixed = (
            self._tokens(prompts.SYSTEM_PROMPT)
            + sum(self._tokens(m["content"]) for m in history_msgs)
            + max(INSTRUCTION_ALLOWANCE, self._tokens(instruction))
        )
        full_total = fixed + sum(s.token_estimate for s in sources)
        forms: dict[str, str] = {}
        if full_total <= cfg.llm.interactive_budget_tokens or not sources:
            strategy: Strategy = "single_shot"
            forms = {s.source_id: "full" for s in sources}
        else:
            strategy = "notes"
            fixed += cfg.llm.passage_budget_tokens
            for source in sources:
                if source.source_id in notes:
                    forms[source.source_id] = "notes"
                elif source.token_estimate < cfg.notes.min_source_tokens:
                    forms[source.source_id] = "full"
                else:
                    return None
            self._fit_forms(sources, notes, forms, fixed, instruction)
            if fixed + self._form_tokens(sources, notes, forms) > cfg.llm.synthesis_budget_tokens:
                return None
        blocks: list[str] = []
        searchable: list[tuple[int, ProcessedSource]] = []
        for index, source in enumerate(sources, start=1):
            form = forms[source.source_id]
            if form == "full":
                blocks.append(prompts.source_block(index, source))
                continue
            note = notes[source.source_id]
            body = note.markdown() if form == "notes" else note.overview
            blocks.append(prompts.source_block(index, source, form=form, body=body))  # type: ignore[arg-type]
            searchable.append((index, source))
        prefix: list[ChatMessage] = [{"role": "system", "content": prompts.SYSTEM_PROMPT}]
        if blocks:
            prefix.append({"role": "user", "content": prompts.blocks_message(blocks)})
            prefix.append({"role": "assistant", "content": ACK})
        prefix.extend(history_msgs)
        return FastPlan(strategy=strategy, prefix=prefix, searchable=searchable, forms=forms)

    def _form_tokens(
        self, sources: list[ProcessedSource], notes: dict[str, SourceNotes], forms: dict[str, str]
    ) -> int:
        total = 0
        for source in sources:
            form = forms[source.source_id]
            if form == "full":
                total += source.token_estimate
            elif form == "notes":
                total += notes[source.source_id].token_estimate
            else:
                total += self._tokens(notes[source.source_id].overview)
        return total

    def _fit_forms(
        self,
        sources: list[ProcessedSource],
        notes: dict[str, SourceNotes],
        forms: dict[str, str],
        fixed: int,
        instruction: str,
    ) -> None:
        """Replace notes with overviews until the prompt fits the interactive budget: the least
        relevant sources to the instruction first, or the largest notes first without one."""
        budget = self.config.llm.interactive_budget_tokens
        if fixed + self._form_tokens(sources, notes, forms) <= budget:
            return
        with_notes = [s for s in sources if forms[s.source_id] == "notes"]
        order = list(range(len(with_notes)))
        if instruction.strip():
            docs = [
                retrieval.Passage(i, s.title, i, "", notes[s.source_id].markdown(), 0)
                for i, s in enumerate(with_notes)
            ]
            scores = retrieval.bm25(instruction, docs)
            order.sort(key=lambda i: scores[i])
        else:
            order.sort(key=lambda i: -notes[with_notes[i].source_id].token_estimate)
        for i in order:
            forms[with_notes[i].source_id] = "overview"
            if fixed + self._form_tokens(sources, notes, forms) <= budget:
                return

    def _excerpts(self, plan: FastPlan, instruction: str) -> str:
        budget = self.config.llm.passage_budget_tokens
        if not plan.searchable or budget <= 0:
            return ""
        cpt = self.config.llm.chars_per_token
        passages: list[retrieval.Passage] = []
        for index, source in plan.searchable:
            passages.extend(
                retrieval.split_passages(source.text_markdown, index, source.title, cpt)
            )
        chosen = retrieval.select_passages(instruction, passages, budget)
        return retrieval.excerpts_block(chosen)

    async def prefill(self, plan: FastPlan) -> Usage | None:
        """Read a session's prompt prefix into the server's cache before a question is asked.
        Background work: it yields to answers. Skipped when the server keeps no prompt cache,
        and when some sources are given as overviews, because which sources keep their notes
        then depends on the question."""
        if not self.llm.prompt_cache or "overview" in plan.forms.values():
            return None
        # The template needs a final user turn; the next real question shares everything before.
        messages = [*plan.prefix, {"role": "user", "content": "."}]
        options = self._options(1)
        usage = await self.gate.background(lambda: self.llm.prefill(messages, options))
        key = plan.prefix_hash()
        self._prepared.pop(key, None)
        self._prepared[key] = None
        while len(self._prepared) > PREPARED_MEMORY:
            self._prepared.pop(next(iter(self._prepared)))
        return usage

    # -- public entry ----------------------------------------------------------------------

    async def run(
        self,
        *,
        instruction: str,
        sources: list[ProcessedSource],
        history: list[Message],
        emit: Emit,
        progress: Progress,
        notes: dict[str, SourceNotes] | None = None,
        full_text: bool = False,
        source_languages: list[str] | None = None,
    ) -> SynthesisResult:
        script = answer_script(
            instruction,
            source_languages or [],
            "\n".join(s.text_markdown[:SCRIPT_SAMPLE_CHARS] for s in sources),
        )
        async with self.gate.interactive():
            plan = None if full_text else self.plan_fast(sources, notes or {}, history, instruction)
            if plan is None:
                if not full_text:
                    await progress("Some long sources have no notes; reading their full text")
                return await self._run_full_text(
                    instruction, sources, history, emit, progress, script
                )
            excerpts = self._excerpts(plan, instruction)
            final = prompts.instruction_message(_with_directive(instruction, script), excerpts)
            messages = [*plan.prefix, {"role": "user", "content": final}]
            log.info(
                "synthesis_plan",
                extra={
                    "strategy": plan.strategy,
                    "sources": len(sources),
                    "forms": sorted(plan.forms.values()),
                    "excerpt_tokens": self._tokens(excerpts),
                },
            )
            read_ahead = plan.prefix_hash() in self._prepared
            usage = await self._stream(messages, emit, progress, script.target, read_ahead)
            return SynthesisResult(
                strategy=plan.strategy, usage=self._usage_dict(usage), script=script.target
            )

    @staticmethod
    def _usage_dict(usage: Usage) -> dict[str, Any]:
        return {
            "prompt_tokens": usage.prompt_tokens,
            "completion_tokens": usage.completion_tokens,
            "cached_tokens": usage.cached_tokens,
            "reasoning_tokens": usage.reasoning_tokens,
            **usage.extra,
        }

    async def _stream(
        self,
        messages: list[ChatMessage],
        emit: Emit,
        progress: Progress,
        script_target: str | None,
        read_ahead: bool = False,
    ) -> Usage:
        # Servers truncate silently when a prompt exceeds the context, dropping the sources
        # first. Fail loudly instead; with accurate estimates the planner keeps us under this.
        prompt_tokens = sum(self._tokens(m["content"]) for m in messages)
        room = self.config.llm.num_ctx - self.config.llm.output_reserve_tokens
        if prompt_tokens > room:
            raise LlmError(
                f"The prompt is about {prompt_tokens} tokens but the context window leaves room "
                f"for {room}. Raise llm.synthesis_budget_tokens (and check the model's context "
                "length), or remove sources from this session."
            )
        if read_ahead:
            await progress("The sources were read ahead; reading the question")
        else:
            await progress(
                f"Reading about {prompt_tokens:,} tokens of sources and instruction"
                + ("" if self.llm.prompt_cache else "; long inputs take minutes")
            )
        usage = Usage()
        reasoning = {"tokens": 0, "reported": 0.0}
        pending: set[asyncio.Task[None]] = set()

        def on_reasoning(_: str) -> None:
            reasoning["tokens"] += 1
            now = time.monotonic()
            if now - reasoning["reported"] >= REASONING_REPORT_S:
                reasoning["reported"] = now
                task = asyncio.ensure_future(progress(f"Thinking ({reasoning['tokens']:,} tokens)"))
                pending.add(task)
                task.add_done_callback(pending.discard)

        # llm.think = false answers without a reasoning phase (text starts at once, less
        # structure); otherwise the server's default applies.
        think = self.config.llm.think if isinstance(self.config.llm.think, bool) else None
        options = self._options(think=False if think is False else None)
        # Models drift between Traditional and Simplified Chinese; hold the answer to its script.
        converter = ScriptStream(script_target)
        first = True
        async for delta in self.llm.stream_chat(messages, options, usage, on_reasoning):
            if first:
                first = False
                await progress("Writing")
            text = converter.feed(delta)
            if text:
                await emit(text)
        rest = converter.flush()
        if rest:
            await emit(rest)
        return usage

    async def suggest_title(
        self, instruction: str, answer: str, script_target: str | None = None
    ) -> str | None:
        if not self.config.llm.suggest_titles:
            return None
        messages = [
            {"role": "system", "content": prompts.TITLE_SYSTEM},
            {
                "role": "user",
                "content": f"Instruction: {instruction[:500]}\n\nDocument:\n{answer[:3000]}",
            },
        ]
        # Reasoning models that cannot switch reasoning off think first, and thinking counts
        # against the output limit, so leave real room; the title itself is a few tokens.
        options = self._options(2048, think=False)
        try:
            text = await self.gate.background(lambda: self.llm.complete(messages, options))
        except Exception as exc:
            log.warning("title_suggestion_failed", extra={"error": str(exc)[:200]})
            return None
        title = prompts.clean_title(text)
        return convert_to(title, script_target) if title else None

    # -- full text -------------------------------------------------------------------------

    async def _run_full_text(
        self,
        instruction: str,
        sources: list[ProcessedSource],
        history: list[Message],
        emit: Emit,
        progress: Progress,
        script: AnswerScript,
    ) -> SynthesisResult:
        strategy = self.plan(sources, instruction, history)
        log.info("synthesis_plan", extra={"strategy": strategy, "sources": len(sources)})
        if strategy == "map_reduce":
            await progress("Condensing long sources before writing")
            sources = await self._condense_sources(instruction, sources, progress)
        messages: list[ChatMessage] = [{"role": "system", "content": prompts.SYSTEM_PROMPT}]
        if sources:
            messages.append(
                {
                    "role": "user",
                    "content": prompts.sources_message(sources, condensed=strategy == "map_reduce"),
                }
            )
            messages.append({"role": "assistant", "content": ACK})
        messages.extend(self._history_messages(history))
        messages.append({"role": "user", "content": _with_directive(instruction, script)})
        usage = await self._stream(messages, emit, progress, script.target)
        return SynthesisResult(
            strategy=strategy, usage=self._usage_dict(usage), script=script.target
        )

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
        # Condensing is extraction, not reasoning: skip the reasoning phase where possible.
        text = await self.llm.complete(messages, self._options(max_tokens, think=False))
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


def _with_directive(instruction: str, script: AnswerScript) -> str:
    return f"{instruction.strip()}\n\n{script.directive}" if script.directive else instruction

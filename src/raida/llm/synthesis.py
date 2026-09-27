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
from raida.models import Message, ProcessedSource, SkillUse, SourceNotes, Strategy
from raida.script import (
    FIRM_DIRECTIVES,
    AnswerScript,
    ScriptStream,
    answer_script,
    convert_to,
    han_script,
    regional_target,
    target_for_language,
)
from raida.skills.render import history_text
from raida.transcribe.languages import base_language, written_language_name

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
UNKNOWN = "?"


class ConversationTooLongError(LlmError):
    """The session's conversation, which is never cut, no longer fits the largest prompt."""


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

    def _conversation(self, history: list[Message]) -> list[ChatMessage]:
        """Every finished question and answer of the session, oldest first, word for word:
        follow-ups see the whole conversation. A question whose answer failed, was stopped or is
        still being written is left out, so the model never sees a question without its
        answer. An earlier skill run shows as its command and what the skill does."""
        turns: list[ChatMessage] = []
        asked: Message | None = None
        # A question and its answer share a timestamp; put the question first.
        for msg in sorted(history, key=lambda m: (m.created_at, m.role != "user")):
            if msg.role == "user":
                asked = msg
            elif asked is not None and msg.status == "done" and msg.content.strip():
                question = (
                    history_text(asked.content, asked.skill)
                    if asked.skill is not None
                    else asked.content
                )
                turns.append({"role": "user", "content": question})
                turns.append({"role": "assistant", "content": msg.content})
                asked = None
        return turns

    def _too_long(self, talk: int) -> str:
        cap = self.config.llm.synthesis_budget_tokens
        return (
            f"This session's conversation is about {talk:,} tokens, and with its sources it no "
            f"longer fits the largest prompt raida sends ({cap:,} tokens). Start a new session to "
            "continue. To allow longer sessions, raise llm.synthesis_budget_tokens and give the "
            "model server that much more context."
        )

    def plan(
        self, sources: list[ProcessedSource], instruction: str, history: list[Message]
    ) -> Strategy:
        """Strategy of the full-text path."""
        fixed = self._tokens(prompts.SYSTEM_PROMPT) + self._tokens(instruction)
        fixed += sum(self._tokens(m["content"]) for m in self._conversation(history))
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
        no notes, or even the overviews of every source exceed llm.synthesis_budget_tokens.

        The sources take at most llm.interactive_budget_tokens, chosen without the
        conversation: they stay the same all session, so each answer only adds to a prompt the
        server has cached. The whole conversation comes on top, up to the largest prompt,
        llm.synthesis_budget_tokens. When it does not fit, the sources least related to the
        question give way to their overviews; the conversation itself is never cut
        (ConversationTooLongError when even that is not enough)."""
        cfg = self.config
        conversation = self._conversation(history)
        talk = sum(self._tokens(m["content"]) for m in conversation)
        cap = cfg.llm.synthesis_budget_tokens
        fixed = self._tokens(prompts.SYSTEM_PROMPT) + max(
            INSTRUCTION_ALLOWANCE, self._tokens(instruction)
        )
        full_total = fixed + sum(s.token_estimate for s in sources)
        forms: dict[str, str] = {s.source_id: "full" for s in sources}
        if not sources or (
            full_total <= cfg.llm.interactive_budget_tokens and full_total + talk <= cap
        ):
            strategy: Strategy = "single_shot"
            if full_total + talk > cap:
                raise ConversationTooLongError(self._too_long(talk))
        else:
            strategy = "notes"
            fixed += cfg.llm.passage_budget_tokens
            for source in sources:
                if source.source_id in notes:
                    forms[source.source_id] = "notes"
                elif source.token_estimate >= cfg.notes.min_source_tokens:
                    return None
            self._fit_forms(
                sources, notes, forms, fixed, instruction, cfg.llm.interactive_budget_tokens
            )
            if fixed + self._form_tokens(sources, notes, forms) + talk > cap:
                self._fit_forms(sources, notes, forms, fixed + talk, instruction, cap)
            used = fixed + self._form_tokens(sources, notes, forms)
            if used > cap:
                return None  # too many sources even as overviews: the full-text path condenses
            if used + talk > cap:
                raise ConversationTooLongError(self._too_long(talk))
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
        prefix.extend(conversation)
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
        budget: int,
    ) -> None:
        """Replace notes with overviews until the prompt fits ``budget``: the least relevant
        sources to the instruction first, or the largest notes first without one."""
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
        skill: SkillUse | None = None,
    ) -> SynthesisResult:
        """Answer ``instruction``. For a skill run, ``instruction`` is the rendered skill and
        ``skill`` says how to pick the answer's language, whether to reason first, and which
        words to search passages for (what the user typed, else the skill's instructions)."""
        languages = source_languages or []
        sample = "\n".join(s.text_markdown[:SCRIPT_SAMPLE_CHARS] for s in sources)
        if skill is None:
            script = answer_script(instruction, languages, sample)
            search = instruction
        else:
            script = skill_script(skill, sources, languages, sample)
            search = skill.arguments or skill.body
        think = False if skill is not None and not skill.think else None
        async with self.gate.interactive():
            plan = None if full_text else self.plan_fast(sources, notes or {}, history, instruction)
            if plan is None:
                if not full_text:
                    await progress("Some long sources have no notes; reading their full text")
                return await self._run_full_text(
                    instruction, sources, history, emit, progress, script, think
                )
            excerpts = self._excerpts(plan, search)
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
            usage = await self._stream(messages, emit, progress, script.target, read_ahead, think)
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
        think_override: bool | None = None,
    ) -> Usage:
        # Servers truncate silently when a prompt exceeds the context, dropping the sources
        # first. Fail loudly instead; with accurate estimates the planner keeps us under this.
        prompt_tokens = sum(self._tokens(m["content"]) for m in messages)
        room = self.config.llm.num_ctx - self.config.llm.output_reserve_tokens
        if prompt_tokens > room:
            raise LlmError(
                f"The prompt is about {prompt_tokens} tokens but the context window leaves room "
                f"for {room}. Remove sources from this session, start a new session if its "
                "conversation has grown long, or raise llm.synthesis_budget_tokens (and check "
                "the model's context length)."
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
        if think_override is False:  # a skill set to answer without thinking first
            think = False
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
        think: bool | None = None,
    ) -> SynthesisResult:
        strategy = self.plan(sources, instruction, history)
        log.info("synthesis_plan", extra={"strategy": strategy, "sources": len(sources)})
        conversation = self._conversation(history)
        if strategy == "map_reduce":
            # Condense the sources into what the whole conversation leaves of the largest prompt.
            talk = sum(self._tokens(m["content"]) for m in conversation)
            asked = self._tokens(instruction)
            floor = self._per_source_floor(len(sources))
            if floor + asked + talk > self.config.llm.synthesis_budget_tokens >= floor + asked:
                raise ConversationTooLongError(self._too_long(talk))
            await progress("Condensing long sources before writing")
            sources = await self._condense_sources(instruction, sources, progress, talk + asked)
        messages: list[ChatMessage] = [{"role": "system", "content": prompts.SYSTEM_PROMPT}]
        if sources:
            messages.append(
                {
                    "role": "user",
                    "content": prompts.sources_message(sources, condensed=strategy == "map_reduce"),
                }
            )
            messages.append({"role": "assistant", "content": ACK})
        messages.extend(conversation)
        messages.append({"role": "user", "content": _with_directive(instruction, script)})
        usage = await self._stream(messages, emit, progress, script.target, think_override=think)
        return SynthesisResult(
            strategy=strategy, usage=self._usage_dict(usage), script=script.target
        )

    def _per_source_budget(self, count: int, reserved: int = 0) -> int:
        """Tokens each condensed source may take, after the system prompt and ``reserved``
        (the conversation and the instruction)."""
        cfg = self.config.llm
        available = (
            cfg.synthesis_budget_tokens - self._tokens(prompts.SYSTEM_PROMPT) - 1500 - reserved
        )
        return max(cfg.condensation_target_tokens, available // max(1, count))

    def _per_source_floor(self, count: int) -> int:
        """The smallest prompt condensing can reach: every source at its condensation target."""
        cfg = self.config.llm
        return self._tokens(prompts.SYSTEM_PROMPT) + 1500 + count * cfg.condensation_target_tokens

    async def _condense_sources(
        self,
        instruction: str,
        sources: list[ProcessedSource],
        progress: Progress,
        reserved: int = 0,
    ) -> list[ProcessedSource]:
        per_source = self._per_source_budget(len(sources), reserved)
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


def main_source_language(sources: list[ProcessedSource], languages: list[str]) -> str | None:
    """The language most of the sources' text is in, weighted by length: the language set on
    each source, or the one detected from its speech; for a source left on automatic, Chinese
    when its text is mostly Chinese characters, otherwise unknown. None when unknown wins."""
    weights: dict[str, int] = {}
    for index, source in enumerate(sources):
        code = languages[index] if index < len(languages) else "auto"
        if not base_language(code):
            code = str(source.meta.get("detected_language") or "auto")
        if not base_language(code):
            code = _chinese_code(source.text_markdown[:SCRIPT_SAMPLE_CHARS]) or UNKNOWN
        weights[code] = weights.get(code, 0) + max(1, source.token_estimate)
    if not weights:
        return None
    main = max(weights, key=lambda c: weights[c])
    return None if main == UNKNOWN else main


def _chinese_code(sample: str) -> str | None:
    letters = sum(ch.isalpha() for ch in sample)
    chinese = sum(1 for ch in sample if "\u4e00" <= ch <= "\u9fff")
    script = han_script(sample)
    if script is None or chinese < letters * 0.3:
        return None
    return "zh-Hant" if script == "hant" else "zh"


def skill_script(
    skill: SkillUse, sources: list[ProcessedSource], languages: list[str], sample: str
) -> AnswerScript:
    """Language directive and Chinese script target for a skill run, from its language option:
    "instructions" follows the language of the skill's instructions and what the user added
    (as a normal message does); "sources" the main language of the sources; a code, that
    language. Chinese quoted in an answer in another language follows the sources' script."""
    if skill.language == "instructions":
        return answer_script(skill.body, languages, sample)
    from_sources = answer_script("", languages, sample).target
    by_sources = skill.language == "sources"
    code = main_source_language(sources, languages) if by_sources else skill.language
    if code is None:
        return AnswerScript(from_sources, "Write the answer in the main language of the sources.")
    target = target_for_language(code)
    if target is not None:
        if target == "zh-Hant" or (by_sources and base_language(code) == "zh"):
            # Chinese of unknown script or region: take both from the sources' text.
            fallback = "hans" if target == "zh-CN" else "hant"
            target = regional_target(han_script(sample) or fallback, languages)
        return AnswerScript(target, FIRM_DIRECTIVES[target])
    name = written_language_name(code) or code
    reason = ", the main language of the sources" if by_sources else ""
    return AnswerScript(from_sources, f"Write the answer in {name}{reason}.")


def _with_directive(instruction: str, script: AnswerScript) -> str:
    return f"{instruction.strip()}\n\n{script.directive}" if script.directive else instruction

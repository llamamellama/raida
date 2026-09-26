"""Scripted backend for tests and for exercising the UI without a model server."""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import AsyncIterator
from typing import Any

from raida.config import LlmConfig
from raida.llm.base import ChatMessage, GenerationOptions, ReasoningCallback, Usage
from raida.llm.tokens import estimate_tokens
from raida.models import ComponentStatus

_ANCHOR = re.compile(r"\[(?:\d{2}:\d{2}:\d{2}|p\. \d+)\]")
_HAN = re.compile(r"[\u4e00-\u9fff]")


class FakeLlmBackend:
    name = "fake"
    prompt_cache = True

    def __init__(self, config: LlmConfig, delay_s: float = 0.0) -> None:
        self.config = config
        self.model = config.model
        self.delay_s = delay_s
        self.calls: list[list[ChatMessage]] = []
        self.prefills: list[list[ChatMessage]] = []
        self.prefills_done = 0
        self.prefill_delay_s = 0.0  # tests set it to model a server still reading a prompt
        self.options: list[GenerationOptions] = []

    async def aclose(self) -> None:
        return None

    def _script(self, messages: list[ChatMessage]) -> str:
        last = messages[-1]["content"] if messages else ""
        if messages and messages[0]["content"].startswith("Name the session"):
            # Written in Simplified, as real models sometimes do for a Chinese instruction.
            return "Fake title" + (" 简体标题" if _HAN.search(last.split("Document:")[0]) else "")
        excerpt = re.search(r"<excerpt>\n?(.*?)</excerpt>", last, re.DOTALL)
        if excerpt:
            anchors = _ANCHOR.findall(excerpt.group(1))[:5] or ["[p. 1]"]
            return "\n".join(f"- {a} Fake note {i}." for i, a in enumerate(anchors, start=1))
        if "<notes>" in last:
            return "Fake overview of the source."
        joined = "\n".join(m["content"] for m in messages)
        titles = re.findall(r'<source[^>]*title="([^"]*)"', joined)
        text = f"# Fake answer\n\nInstruction: {last.strip()[-200:]}\n\n"
        if titles:
            text += "Sources used:\n\n" + "\n".join(f"- {t}" for t in titles) + "\n"
        # Real models drift into Simplified Chinese; this line lets tests see it corrected.
        return text + "\nFake 中文：我们的内容，头发。\n"

    async def stream_chat(
        self,
        messages: list[ChatMessage],
        options: GenerationOptions,
        usage: Usage | None = None,
        on_reasoning: ReasoningCallback | None = None,
    ) -> AsyncIterator[str]:
        self.calls.append(messages)
        self.options.append(options)
        text = self._script(messages)
        if on_reasoning is not None and options.think is not False:
            on_reasoning("Fake reasoning.")
        for word in text.split(" "):
            if self.delay_s:
                await asyncio.sleep(self.delay_s)
            yield word + " "
        if usage is not None:
            joined = "\n".join(m["content"] for m in messages)
            usage.prompt_tokens = estimate_tokens(joined, self.config.chars_per_token)
            usage.completion_tokens = estimate_tokens(text, self.config.chars_per_token)

    async def complete(self, messages: list[ChatMessage], options: GenerationOptions) -> str:
        return "".join([d async for d in self.stream_chat(messages, options)]).strip()

    async def complete_json(
        self, messages: list[ChatMessage], schema: dict[str, Any], options: GenerationOptions
    ) -> dict[str, Any]:
        self.calls.append(messages)
        result: dict[str, Any] = {}
        for key, spec in schema.get("properties", {}).items():
            result[key] = "Fake title" if spec.get("type") == "string" else None
        return json.loads(json.dumps(result))

    async def prefill(self, messages: list[ChatMessage], options: GenerationOptions) -> Usage:
        self.prefills.append(messages)
        if self.prefill_delay_s:
            await asyncio.sleep(self.prefill_delay_s)
        self.prefills_done += 1
        joined = "\n".join(m["content"] for m in messages)
        return Usage(prompt_tokens=estimate_tokens(joined, self.config.chars_per_token))

    async def count_tokens(self, text: str) -> int:
        return estimate_tokens(text, self.config.chars_per_token)

    async def health(self) -> ComponentStatus:
        return ComponentStatus(
            ok=True, detail="fake LLM backend (tests only)", extra={"model": self.model}
        )

    async def warm_up(self, num_ctx: int) -> ComponentStatus:
        return await self.health()

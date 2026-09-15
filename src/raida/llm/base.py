"""LLM backend protocol shared by Ollama, OpenAI-compatible servers and the test fake."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any, Protocol

from raida.models import ComponentStatus

ChatMessage = dict[str, str]


@dataclass
class GenerationOptions:
    num_ctx: int
    max_tokens: int
    temperature: float = 0.3


@dataclass
class Usage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    extra: dict[str, Any] = field(default_factory=dict)


class LlmError(RuntimeError):
    pass


class LlmBackend(Protocol):
    name: str
    model: str

    def stream_chat(
        self, messages: list[ChatMessage], options: GenerationOptions, usage: Usage | None = None
    ) -> AsyncIterator[str]: ...

    async def complete(self, messages: list[ChatMessage], options: GenerationOptions) -> str: ...

    async def complete_json(
        self, messages: list[ChatMessage], schema: dict[str, Any], options: GenerationOptions
    ) -> dict[str, Any]: ...

    async def count_tokens(self, text: str) -> int: ...

    async def health(self) -> ComponentStatus: ...

    async def warm_up(self, num_ctx: int) -> ComponentStatus: ...

    async def aclose(self) -> None: ...

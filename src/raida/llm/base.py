"""LLM backend protocol shared by llama-server, Ollama, OpenAI-compatible servers and the fake."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

from raida.models import ComponentStatus

ChatMessage = dict[str, str]
# Called with each piece of hidden reasoning a thinking model streams before its answer.
ReasoningCallback = Callable[[str], None]


@dataclass
class GenerationOptions:
    num_ctx: int
    max_tokens: int
    temperature: float = 0.3
    # False asks the backend to answer without a reasoning phase (note taking, titles): a
    # thinking model otherwise spends most of such a call reasoning. None keeps the server's
    # behaviour. Backends that cannot turn reasoning off ignore it; the output is still correct.
    think: bool | None = None


@dataclass
class Usage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    # Prompt tokens served from the server's prompt cache instead of being read again.
    cached_tokens: int = 0
    reasoning_tokens: int = 0
    extra: dict[str, Any] = field(default_factory=dict)


class LlmError(RuntimeError):
    pass


class LlmBackend(Protocol):
    name: str
    model: str
    # True when the server keeps processed prompts and reuses a matching prefix, so reading a
    # session's sources ahead of the question (prefill) makes the answer start at once.
    prompt_cache: bool

    def stream_chat(
        self,
        messages: list[ChatMessage],
        options: GenerationOptions,
        usage: Usage | None = None,
        on_reasoning: ReasoningCallback | None = None,
    ) -> AsyncIterator[str]: ...

    async def complete(self, messages: list[ChatMessage], options: GenerationOptions) -> str: ...

    async def complete_json(
        self, messages: list[ChatMessage], schema: dict[str, Any], options: GenerationOptions
    ) -> dict[str, Any]: ...

    async def prefill(self, messages: list[ChatMessage], options: GenerationOptions) -> Usage:
        """Read the prompt into the server's cache without generating an answer."""
        ...

    async def count_tokens(self, text: str) -> int: ...

    async def health(self) -> ComponentStatus: ...

    async def warm_up(self, num_ctx: int) -> ComponentStatus: ...

    async def aclose(self) -> None: ...

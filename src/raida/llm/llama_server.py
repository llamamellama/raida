"""llama.cpp's llama-server: the recommended backend on Apple Silicon.

On top of the OpenAI-compatible API it offers what makes answers fast here:

- a prompt cache per slot plus a RAM cache of idle slots (``--cache-ram``), so a session whose
  sources were read once answers the next question after reading only the new tokens;
- ``max_tokens: 0`` requests that only read a prompt, used to prepare a session in advance;
- ``/apply-template`` and raw ``/completion``, used to close the reasoning block of a
  thinking-only model so note taking does not spend minutes reasoning;
- ``/tokenize`` for exact token counts and ``/props`` for the context size actually allocated.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import httpx

from raida.config import LlmConfig
from raida.llm.base import ChatMessage, GenerationOptions, LlmError, ReasoningCallback, Usage
from raida.llm.openai_compat import OpenAiCompatibleBackend
from raida.models import ComponentStatus

# Chat templates of thinking-only models (Qwen3 Thinking 2507, DeepSeek R1 and others) end the
# generation prompt inside an opened reasoning block; closing it makes the model answer at once.
OPEN_THINK = "<think>\n"
CLOSE_THINK = "\n</think>\n\n"


class LlamaServerBackend(OpenAiCompatibleBackend):
    name = "llama_server"
    prompt_cache = True

    def __init__(self, config: LlmConfig) -> None:
        super().__init__(config)
        self._required_ctx = config.num_ctx

    def _payload(
        self, messages: list[ChatMessage], options: GenerationOptions, stream: bool
    ) -> dict[str, Any]:
        payload = super()._payload(messages, options, stream)
        payload["cache_prompt"] = True
        if options.think is False:
            # Honoured by hybrid templates (Qwen3 2504, Qwen3.5); ignored by the others.
            payload["chat_template_kwargs"] = {"enable_thinking": False}
        return payload

    def stream_chat(
        self,
        messages: list[ChatMessage],
        options: GenerationOptions,
        usage: Usage | None = None,
        on_reasoning: ReasoningCallback | None = None,
    ) -> AsyncIterator[str]:
        if options.think is False:
            return self._stream_without_reasoning(messages, options, usage, on_reasoning)
        return super().stream_chat(messages, options, usage, on_reasoning)

    async def _apply_template(self, messages: list[ChatMessage]) -> str:
        body = {"messages": messages, "chat_template_kwargs": {"enable_thinking": False}}
        try:
            resp = await self._client.post("/apply-template", json=body)
        except httpx.HTTPError as exc:
            raise LlmError(f"LLM request failed: {type(exc).__name__}: {exc}") from exc
        if resp.status_code >= 400:
            raise LlmError(f"llama-server returned {resp.status_code}: {resp.text[:300]}")
        return str(resp.json().get("prompt", ""))

    async def _stream_without_reasoning(
        self,
        messages: list[ChatMessage],
        options: GenerationOptions,
        usage: Usage | None,
        on_reasoning: ReasoningCallback | None,
    ) -> AsyncIterator[str]:
        prompt = await self._apply_template(messages)
        if not prompt.endswith(OPEN_THINK):
            # The template leaves reasoning to the model (or has none): let the server parse it.
            async for delta in super().stream_chat(messages, options, usage, on_reasoning):
                yield delta
            return
        payload = {
            # "<think>\n" + "\n</think>\n\n": the empty block the model emits when it skips
            # reasoning, as rendered by these templates for earlier turns.
            "prompt": prompt + CLOSE_THINK,
            "n_predict": options.max_tokens,
            "temperature": options.temperature,
            "cache_prompt": True,
            "stream": True,
        }
        try:
            async with self._client.stream("POST", "/completion", json=payload) as resp:
                if resp.status_code >= 400:
                    body = (await resp.aread()).decode("utf-8", "replace")
                    raise LlmError(f"llama-server returned {resp.status_code}: {body[:500]}")
                async for line in resp.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    chunk = json.loads(line[5:].strip())
                    if chunk.get("error"):
                        raise LlmError(f"llama-server error: {chunk['error']}")
                    if chunk.get("content"):
                        yield chunk["content"]
                    if chunk.get("stop"):
                        if usage is not None:
                            timings = chunk.get("timings") or {}
                            usage.prompt_tokens = int(chunk.get("tokens_evaluated", 0) or 0)
                            usage.completion_tokens = int(chunk.get("tokens_predicted", 0) or 0)
                            usage.cached_tokens = int(timings.get("cache_n", 0) or 0)
                            usage.extra["timings"] = timings
                        break
        except httpx.TimeoutException as exc:
            raise self._timeout_error(exc) from exc
        except httpx.HTTPError as exc:
            raise LlmError(f"LLM request failed: {type(exc).__name__}: {exc}") from exc

    async def prefill(self, messages: list[ChatMessage], options: GenerationOptions) -> Usage:
        payload = self._payload(
            messages,
            GenerationOptions(num_ctx=options.num_ctx, max_tokens=0, temperature=0.0),
            stream=False,
        )
        try:
            resp = await self._client.post("/v1/chat/completions", json=payload)
        except httpx.TimeoutException as exc:
            raise self._timeout_error(exc) from exc
        except httpx.HTTPError as exc:
            raise LlmError(f"LLM request failed: {type(exc).__name__}: {exc}") from exc
        if resp.status_code >= 400:
            raise LlmError(f"llama-server returned {resp.status_code}: {resp.text[:500]}")
        usage = Usage()
        data = resp.json()
        self._record_usage(usage, data.get("usage") or {})
        usage.extra["timings"] = data.get("timings") or {}
        return usage

    async def health(self) -> ComponentStatus:
        resp = await self._client.get("/health")
        if resp.status_code == 503:
            return ComponentStatus(ok=False, detail="llama-server is still loading the model")
        resp.raise_for_status()
        props = (await self._client.get("/props")).json()
        settings = props.get("default_generation_settings") or {}
        n_ctx = int(settings.get("n_ctx", 0) or 0)
        alias = str(props.get("model_alias") or "")
        slots = int(props.get("total_slots", 0) or 0)
        extra: dict[str, Any] = {
            "model": self.model,
            "alias": alias,
            "context_length": n_ctx,
            "slots": slots,
            "build": props.get("build_info"),
            "sleeping": bool(props.get("is_sleeping", False)),
        }
        detail = f"llama-server {alias or '?'}: context {n_ctx:,}, {slots} slots"
        if alias and alias != self.model:
            return ComponentStatus(
                ok=False,
                detail=f"llama-server serves '{alias}' but llm.model is '{self.model}'. Start "
                f"it with --alias {self.model} or change llm.model.",
                extra=extra,
            )
        if n_ctx and n_ctx < self._required_ctx:
            return ComponentStatus(
                ok=False,
                detail=f"{detail}, but raida needs {self._required_ctx:,} "
                "(llm.synthesis_budget_tokens + output_reserve_tokens + prompt_overhead_tokens). "
                "Start llama-server with a larger -c or lower llm.synthesis_budget_tokens.",
                extra=extra,
            )
        if extra["sleeping"]:
            detail += ", asleep (the next request reloads the model)"
        return ComponentStatus(ok=True, detail=detail, extra=extra)

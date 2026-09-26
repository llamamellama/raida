"""OpenAI-compatible backend for LM Studio, mlx_lm.server, vLLM and similar servers.

llama.cpp's llama-server has its own subclass in ``llama_server.py`` that adds prompt-cache
prefill, exact token counts and answers without a reasoning phase.
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from typing import Any

import httpx

from raida.config import LlmConfig
from raida.llm.base import ChatMessage, GenerationOptions, LlmError, ReasoningCallback, Usage
from raida.llm.tokens import estimate_tokens
from raida.models import ComponentStatus

log = logging.getLogger(__name__)


class OpenAiCompatibleBackend:
    name = "openai_compatible"
    prompt_cache = False

    def __init__(self, config: LlmConfig) -> None:
        self.config = config
        self.model = config.model
        base = config.base_url.rstrip("/")
        self._root = base[: -len("/v1")] if base.endswith("/v1") else base
        self._client = httpx.AsyncClient(
            base_url=self._root,
            timeout=httpx.Timeout(config.request_timeout_s, connect=10.0),
            headers={"Authorization": f"Bearer {config.api_key}"},
        )
        self._tokenize_supported: bool | None = None

    async def aclose(self) -> None:
        await self._client.aclose()

    def _payload(
        self, messages: list[ChatMessage], options: GenerationOptions, stream: bool
    ) -> dict[str, Any]:
        return {
            "model": self.model,
            "messages": messages,
            "stream": stream,
            "max_tokens": options.max_tokens,
            "temperature": options.temperature,
        }

    @staticmethod
    def _record_usage(usage: Usage, data: dict[str, Any]) -> None:
        usage.prompt_tokens = int(data.get("prompt_tokens", 0) or 0)
        usage.completion_tokens = int(data.get("completion_tokens", 0) or 0)
        details = data.get("prompt_tokens_details") or {}
        usage.cached_tokens = int(details.get("cached_tokens", 0) or 0)

    def _timeout_error(self, exc: Exception) -> LlmError:
        return LlmError(
            f"The LLM server did not answer within llm.request_timeout_s = "
            f"{self.config.request_timeout_s:g} s. It is silent while it reads the prompt, and "
            "a very long prompt can take many minutes; raise the timeout or reduce the sources."
        )

    async def _stream(
        self,
        payload: dict[str, Any],
        usage: Usage | None,
        on_reasoning: ReasoningCallback | None,
    ) -> AsyncIterator[str]:
        payload["stream_options"] = {"include_usage": True}
        try:
            async with self._client.stream("POST", "/v1/chat/completions", json=payload) as resp:
                if resp.status_code >= 400:
                    body = (await resp.aread()).decode("utf-8", "replace")
                    raise LlmError(f"LLM server returned {resp.status_code}: {body[:500]}")
                async for line in resp.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        break
                    chunk = json.loads(data)
                    if chunk.get("error"):
                        raise LlmError(f"LLM server error: {chunk['error']}")
                    if usage is not None and chunk.get("usage"):
                        self._record_usage(usage, chunk["usage"])
                        if chunk.get("timings"):
                            usage.extra["timings"] = chunk["timings"]
                    for choice in chunk.get("choices", []):
                        delta = choice.get("delta", {})
                        reasoning = delta.get("reasoning_content") or delta.get("reasoning")
                        if reasoning:
                            if usage is not None:
                                usage.reasoning_tokens += 1
                            if on_reasoning is not None:
                                on_reasoning(reasoning)
                        content = delta.get("content")
                        if content:
                            yield content
        except httpx.TimeoutException as exc:
            raise self._timeout_error(exc) from exc
        except httpx.HTTPError as exc:
            raise LlmError(f"LLM request failed: {type(exc).__name__}: {exc}") from exc

    def stream_chat(
        self,
        messages: list[ChatMessage],
        options: GenerationOptions,
        usage: Usage | None = None,
        on_reasoning: ReasoningCallback | None = None,
    ) -> AsyncIterator[str]:
        return self._stream(self._payload(messages, options, stream=True), usage, on_reasoning)

    async def complete(self, messages: list[ChatMessage], options: GenerationOptions) -> str:
        return "".join([d async for d in self.stream_chat(messages, options)])

    async def complete_json(
        self, messages: list[ChatMessage], schema: dict[str, Any], options: GenerationOptions
    ) -> dict[str, Any]:
        payload = self._payload(messages, options, stream=False)
        payload["response_format"] = {
            "type": "json_schema",
            "json_schema": {"name": "raida_output", "schema": schema, "strict": True},
        }
        try:
            resp = await self._client.post("/v1/chat/completions", json=payload)
        except httpx.TimeoutException as exc:
            raise self._timeout_error(exc) from exc
        except httpx.HTTPError as exc:
            raise LlmError(f"LLM request failed: {exc}") from exc
        if resp.status_code >= 400:
            raise LlmError(f"LLM server returned {resp.status_code}: {resp.text[:500]}")
        content = resp.json()["choices"][0]["message"]["content"]
        try:
            return json.loads(content)
        except json.JSONDecodeError as exc:
            raise LlmError(f"Model returned invalid JSON: {content[:200]}") from exc

    async def prefill(self, messages: list[ChatMessage], options: GenerationOptions) -> Usage:
        usage = Usage()
        short = GenerationOptions(num_ctx=options.num_ctx, max_tokens=1, temperature=0.0)
        async for _ in self.stream_chat(messages, short, usage):
            pass
        return usage

    async def count_tokens(self, text: str) -> int:
        if self._tokenize_supported is not False:
            try:
                resp = await self._client.post("/tokenize", json={"content": text})
                if resp.status_code == 200:
                    self._tokenize_supported = True
                    return len(resp.json().get("tokens", []))
            except httpx.HTTPError:
                pass
            self._tokenize_supported = False
        return estimate_tokens(text, self.config.chars_per_token)

    async def health(self) -> ComponentStatus:
        resp = await self._client.get("/v1/models")
        resp.raise_for_status()
        ids = [m.get("id", "") for m in resp.json().get("data", [])]
        if ids and self.model not in ids:
            return ComponentStatus(
                ok=False,
                detail=f"model '{self.model}' not served. Served: {', '.join(ids[:10])}",
            )
        return ComponentStatus(
            ok=True,
            detail=f"openai-compatible server at {self._root}, model {self.model}",
            extra={"model": self.model, "served": ids},
        )

    async def warm_up(self, num_ctx: int) -> ComponentStatus:
        options = GenerationOptions(num_ctx=num_ctx, max_tokens=1, temperature=0.0)
        await self.complete([{"role": "user", "content": "Reply with OK."}], options)
        return await self.health()

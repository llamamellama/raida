"""Ollama backend using the native API (streaming NDJSON, num_ctx, JSON-schema format)."""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from typing import Any

import httpx

from raida.config import LlmConfig
from raida.llm.base import ChatMessage, GenerationOptions, LlmError, Usage
from raida.llm.tokens import estimate_tokens
from raida.models import ComponentStatus

log = logging.getLogger(__name__)


def _same_model(a: str, b: str) -> bool:
    norm = lambda s: s if ":" in s else f"{s}:latest"  # noqa: E731
    return norm(a) == norm(b)


class OllamaBackend:
    name = "ollama"

    def __init__(self, config: LlmConfig) -> None:
        self.config = config
        self.model = config.model
        self._client = httpx.AsyncClient(
            base_url=config.base_url.rstrip("/"),
            timeout=httpx.Timeout(config.request_timeout_s, connect=10.0),
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    def _payload(
        self, messages: list[ChatMessage], options: GenerationOptions, stream: bool
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "stream": stream,
            "keep_alive": self.config.keep_alive,
            "options": {
                "num_ctx": options.num_ctx,
                "num_predict": options.max_tokens,
                "temperature": options.temperature,
            },
        }
        if self.config.think is not None:
            payload["think"] = self.config.think
        return payload

    async def stream_chat(
        self, messages: list[ChatMessage], options: GenerationOptions, usage: Usage | None = None
    ) -> AsyncIterator[str]:
        payload = self._payload(messages, options, stream=True)
        try:
            async with self._client.stream("POST", "/api/chat", json=payload) as response:
                if response.status_code >= 400:
                    body = (await response.aread()).decode("utf-8", "replace")
                    raise LlmError(f"Ollama returned {response.status_code}: {body[:500]}")
                async for line in response.aiter_lines():
                    if not line.strip():
                        continue
                    chunk = json.loads(line)
                    if chunk.get("error"):
                        raise LlmError(f"Ollama error: {chunk['error']}")
                    delta = chunk.get("message", {}).get("content", "")
                    if delta:
                        yield delta
                    if chunk.get("done"):
                        if usage is not None:
                            usage.prompt_tokens = int(chunk.get("prompt_eval_count", 0) or 0)
                            usage.completion_tokens = int(chunk.get("eval_count", 0) or 0)
                            usage.extra = {
                                k: chunk[k]
                                for k in ("total_duration", "prompt_eval_duration", "eval_duration")
                                if k in chunk
                            }
                        break
        except httpx.TimeoutException as exc:
            raise LlmError(
                f"Ollama did not answer within llm.request_timeout_s = "
                f"{self.config.request_timeout_s:g} s. The server is silent while it reads the "
                "prompt, and a very long prompt can take many minutes; raise the timeout or "
                "reduce the sources."
            ) from exc
        except httpx.HTTPError as exc:
            raise LlmError(f"Ollama request failed: {type(exc).__name__}: {exc}") from exc

    async def complete(self, messages: list[ChatMessage], options: GenerationOptions) -> str:
        parts = [delta async for delta in self.stream_chat(messages, options)]
        return "".join(parts)

    async def complete_json(
        self, messages: list[ChatMessage], schema: dict[str, Any], options: GenerationOptions
    ) -> dict[str, Any]:
        payload = self._payload(messages, options, stream=False)
        payload["format"] = schema
        try:
            response = await self._client.post("/api/chat", json=payload)
        except httpx.HTTPError as exc:
            raise LlmError(f"Ollama request failed: {exc}") from exc
        if response.status_code >= 400:
            raise LlmError(f"Ollama returned {response.status_code}: {response.text[:500]}")
        content = response.json().get("message", {}).get("content", "")
        try:
            return json.loads(content)
        except json.JSONDecodeError as exc:
            raise LlmError(f"Model returned invalid JSON: {content[:200]}") from exc

    async def count_tokens(self, text: str) -> int:
        return estimate_tokens(text, self.config.chars_per_token)

    async def _tags(self) -> list[dict[str, Any]]:
        response = await self._client.get("/api/tags")
        response.raise_for_status()
        return response.json().get("models", [])

    async def _ps(self) -> list[dict[str, Any]]:
        response = await self._client.get("/api/ps")
        response.raise_for_status()
        return response.json().get("models", [])

    async def health(self) -> ComponentStatus:
        tags = await self._tags()
        present = [m for m in tags if _same_model(m.get("name", ""), self.model)]
        if not present:
            available = ", ".join(m.get("name", "?") for m in tags[:10]) or "none"
            return ComponentStatus(
                ok=False,
                detail=f"model '{self.model}' is not pulled. Available: {available}. "
                f"Run `ollama pull {self.model}`.",
            )
        size_gb = present[0].get("size", 0) / 1024**3
        extra: dict[str, Any] = {"model": self.model, "size_gb": round(size_gb, 1)}
        detail = f"ollama {self.model} ({size_gb:.1f} GB on disk)"
        loaded = [m for m in await self._ps() if _same_model(m.get("name", ""), self.model)]
        if loaded:
            info = loaded[0]
            ctx = int(info.get("context_length", 0) or 0)
            size, vram = int(info.get("size", 0) or 0), int(info.get("size_vram", 0) or 0)
            gpu_pct = round(100 * vram / size) if size else None
            extra.update({"loaded": True, "context_length": ctx, "gpu_percent": gpu_pct})
            detail += f", loaded: context {ctx}, GPU {gpu_pct}%"
            if ctx and ctx < self.config.num_ctx:
                return ComponentStatus(
                    ok=False,
                    detail=detail + f" but {self.config.num_ctx} requested; the running model "
                    f"ignores num_ctx (known issue with some -mlx tags). Try the GGUF tag.",
                    extra=extra,
                )
            if gpu_pct is not None and gpu_pct < 100:
                detail += (
                    " (partial CPU offload: expect slow prefill; free memory or pick a "
                    "smaller model)"
                )
        else:
            extra["loaded"] = False
            detail += ", not loaded yet"
        return ComponentStatus(ok=True, detail=detail, extra=extra)

    async def warm_up(self, num_ctx: int) -> ComponentStatus:
        options = GenerationOptions(num_ctx=num_ctx, max_tokens=1, temperature=0.0)
        await self.complete([{"role": "user", "content": "Reply with OK."}], options)
        return await self.health()

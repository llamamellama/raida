from __future__ import annotations

from raida.config import Config
from raida.llm.base import LlmBackend


def build_llm_backend(config: Config) -> LlmBackend:
    backend = config.llm.backend
    if backend == "llama_server":
        from raida.llm.llama_server import LlamaServerBackend

        return LlamaServerBackend(config.llm)
    if backend == "ollama":
        from raida.llm.ollama import OllamaBackend

        return OllamaBackend(config.llm)
    if backend == "openai_compatible":
        from raida.llm.openai_compat import OpenAiCompatibleBackend

        return OpenAiCompatibleBackend(config.llm)
    if backend == "fake":
        from raida.llm.fake import FakeLlmBackend

        return FakeLlmBackend(config.llm)
    raise ValueError(f"Unknown llm backend: {backend}")

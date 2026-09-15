# ADR-0002: The LLM runs in a separate local server process behind an HTTP API

- Status: accepted
- Date: 2026-09-15

## Context

Loading a 60-120 GB model inside the application process would tie model lifetime to the app,
make restarts slow, and duplicate the model-management features (pulling, caching, keep-alive,
GPU placement) that local servers already provide. Ollama, llama.cpp's `llama-server`, LM Studio
and `mlx_lm.server` all expose HTTP APIs and run natively on Apple Silicon.

## Decision

raida talks to a model server over HTTP on localhost through an `LlmBackend` protocol with two
implementations: the Ollama native API (streaming, per-request `num_ctx`, JSON-schema `format`,
`keep_alive`, `/api/ps` assertions) and a generic OpenAI-compatible client for the other servers.
Ollama is the documented default because it handles model downloads and keep-alive; llama-server
is the documented alternative for long-context tuning. The model name is required configuration
with no default. Which model to run is a configuration choice; see `docs/model-setup.md`.

## Consequences

- The app never loads weights; the server keeps the model warm across app restarts.
- Startup verifies that the configured model is present and, after a warm-up request, that the
  server honours the requested context length and runs fully on the GPU.
- Token counts are estimated locally and corrected from server-reported usage after each call.
- No LangChain, LlamaIndex or LiteLLM: the client is two small modules on top of httpx.

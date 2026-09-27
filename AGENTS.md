# Agent instructions for raida

Read `docs/architecture.md` before changing code. The decisions that shape this repo are in
`docs/adr/`.

## Conventions

- Python 3.12, managed with `uv`. Run everything through `uv run`.
- Exact-pinned dependencies in `pyproject.toml`; `uv.lock` is committed. Add a dependency only
  with a version you tested, after checking it is maintained.
- Fail fast: validate at boundaries, raise with clear messages, no silent defaults for values
  that must be configured (`llm.model` is the canonical example).
- Logs are JSON lines on stdout. Never write log files.
- No emojis anywhere: code, comments, docs, commit messages, PR text.
- snake_case Python modules; kebab-case markdown filenames; ADRs as `docs/adr/NNNN-slug.md`.
- Every user-visible change updates `README.md` in the same commit, and adds a line for users
  under `## [Unreleased]` in `CHANGELOG.md` (Keep a Changelog: Added, Changed, Deprecated,
  Removed, Fixed, Security). A release renames that section to the version and date, following
  Semantic Versioning, sets the same version in `pyproject.toml` and
  `src/raida/__init__.py` (then `uv lock`), and tags the release commit `vX.Y.Z`.
- Tests must pass on Linux CI without MLX or Ollama: keep Apple-only imports lazy and use the
  fake LLM and fake transcriber backends in tests.

## Layout

- `src/raida/api` HTTP routes and SSE. `src/raida/pipeline` scheduler and per-kind stages.
- `src/raida/transcribe` speech-to-text backends. `src/raida/llm` model backends and synthesis.
- `src/raida/export` txt/md/pdf/docx renderers. `src/raida/web` static frontend.
- `src/raida/skills` skills (SKILL.md folders; built-in ones in `skills/builtin`).
  `src/raida/script.py` Chinese script handling.

## Commands

- `make setup`, `make llm MODEL=<tag>`, `make ollama`, `make doctor`, `make dev`, `make test`,
  `make lint`, `make fmt`, `make bench [MEDIA=<recording>]`, `make fixtures`.

.PHONY: setup dev doctor ollama test lint fmt bench fixtures

# Homebrew on Apple Silicon installs to /opt/homebrew, which many shells do not have on PATH
# (an Intel Homebrew under /usr/local often comes first). Put it in front for every recipe so
# `make dev` works from any terminal; on other platforms the extra entry is harmless.
export PATH := /opt/homebrew/bin:$(PATH)

# WeasyPrint loads Homebrew's Pango at runtime on macOS; the variable is ignored elsewhere.
MAC_LIBS := DYLD_FALLBACK_LIBRARY_PATH=/opt/homebrew/lib:$(DYLD_FALLBACK_LIBRARY_PATH)

setup:
	./scripts/setup-mac.sh

dev:
	$(MAC_LIBS) uv run raida serve

doctor:
	$(MAC_LIBS) uv run raida doctor

# Foreground Ollama with the settings raida expects; run it in a second terminal.
ollama:
	OLLAMA_NUM_PARALLEL=1 OLLAMA_KEEP_ALIVE=1h OLLAMA_FLASH_ATTENTION=1 \
	OLLAMA_KV_CACHE_TYPE=q8_0 OLLAMA_NO_CLOUD=1 ollama serve

test:
	uv run pytest -q

lint:
	uv run ruff check . && uv run ruff format --check .

fmt:
	uv run ruff format . && uv run ruff check --fix .

bench:
	$(MAC_LIBS) uv run python scripts/bench_llm.py && $(MAC_LIBS) uv run python scripts/bench_stt.py

fixtures:
	uv run python tests/fixtures/make_fixtures.py

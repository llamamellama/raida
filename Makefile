.PHONY: setup dev doctor test lint fmt bench fixtures

setup:
	./scripts/setup-mac.sh

dev:
	uv run raida serve

doctor:
	uv run raida doctor

test:
	uv run pytest -q

lint:
	uv run ruff check . && uv run ruff format --check .

fmt:
	uv run ruff format . && uv run ruff check --fix .

bench:
	uv run python scripts/bench_llm.py && uv run python scripts/bench_stt.py

fixtures:
	uv run python tests/fixtures/make_fixtures.py

.PHONY: help test test-live lint lint-fix format format-check coverage ci

help:
	@echo "make targets:"
	@echo "  test          run unit tests (skips live integration tests)"
	@echo "  test-live     run unit + live integration tests (needs .env, .config)"
	@echo "  lint          run ruff check"
	@echo "  lint-fix      run ruff check with auto-fix"
	@echo "  format        run ruff format on src/ and tests/"
	@echo "  format-check  verify formatting without writing changes"
	@echo "  coverage      run unit tests with a coverage report"
	@echo "  ci            lint + format-check + test (the full local check)"

test:
	uv run pytest

test-live:
	uv run pytest --runlive

lint:
	uv run ruff check .

lint-fix:
	uv run ruff check --fix .

format:
	uv run ruff format .

format-check:
	uv run ruff format --check .

coverage:
	uv run --with pytest-cov pytest --cov=genie_config_optimizer --cov-report=term-missing

ci: lint format-check test

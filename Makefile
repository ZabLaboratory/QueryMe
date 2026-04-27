.PHONY: install lint test typecheck audit secrets-scan all

install:
	uv sync
	uv run pre-commit install

lint:
	uv run ruff check .

test:
	uv run pytest

typecheck:
	uv run mypy src

audit:
	uv run pip-audit --skip-editable

secrets-scan:
	uv run detect-secrets scan --baseline .secrets.baseline

all: lint typecheck test audit

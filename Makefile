.PHONY: install test lint format typecheck run eval sync-skills

install:
	uv sync --all-groups

test:
	uv run pytest

lint:
	uv run ruff check .

format:
	uv run ruff format .

typecheck:
	uv run pyright

run:
	agents-cli run

eval:
	agents-cli eval run

sync-skills:
	uv run python scripts/sync_agents_cli_skills.py

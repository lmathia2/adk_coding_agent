.PHONY: install install-eval test lint format typecheck run eval eval-harbor sync-skills

install:
	uv sync --all-groups

install-eval:
	uv sync --all-groups --extra eval

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

eval-harbor:
	.venv/bin/python scripts/run_harbor_eval.py

sync-skills:
	uv run python scripts/sync_agents_cli_skills.py

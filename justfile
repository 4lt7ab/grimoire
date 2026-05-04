default:
    @just --list

install:
    uv sync

test:
    uv run pytest

lint:
    uv run ruff check

fmt:
    uv run ruff format

check:
    uv run ruff check
    uv run ruff format --check
    uv run pytest

bump level="patch":
    uv run python scripts/bump_version.py {{level}}

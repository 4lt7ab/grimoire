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

publish: _publish-preflight
    #!/usr/bin/env bash
    set -euo pipefail
    if [ -z "${UV_PUBLISH_TOKEN:-}" ]; then
        read -rsp "PyPI API token: " UV_PUBLISH_TOKEN
        echo
        export UV_PUBLISH_TOKEN
    fi
    rm -rf dist
    uv build --all-packages
    uv publish

test-publish: _publish-preflight
    #!/usr/bin/env bash
    set -euo pipefail
    if [ -z "${UV_PUBLISH_TOKEN:-}" ]; then
        read -rsp "TestPyPI API token: " UV_PUBLISH_TOKEN
        echo
        export UV_PUBLISH_TOKEN
    fi
    rm -rf dist
    uv build --all-packages
    uv publish --publish-url https://test.pypi.org/legacy/

_publish-preflight:
    @if [ -n "$(git status --porcelain)" ]; then echo "working tree is dirty; commit or stash before publishing" && exit 1; fi

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
    HF_HUB_OFFLINE=1 uv run pytest

# Prime the shared fastembed model cache. Run once; `just check` is offline after.
warm:
    @echo "Priming model cache at .local/grimoire-test-models/ ..."
    @HF_HUB_DISABLE_XET=1 uv run python -c "from grimoire.embedders import FastembedEmbedder; FastembedEmbedder(cache_folder='.local/grimoire-test-models')"
    @echo "Cache warm. 'just check' now runs offline."

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

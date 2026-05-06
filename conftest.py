"""Shared pytest fixtures for both packages.

Visible to every test under `packages/*/tests/` because pytest discovers
conftest files by walking up from each test file to the rootdir (this repo).
"""

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent
SHARED_MODELS_CACHE = REPO_ROOT / ".grimoire" / "models"


@pytest.fixture(scope="session")
def _shared_models_cache() -> Path:
    """Repo-local fastembed cache shared across all tests.

    Prime once with `just init`; thereafter `just check` runs fully offline
    via `HF_HUB_OFFLINE=1`. A cold cache surfaces as a clear fastembed error
    rather than a silent re-download.
    """
    SHARED_MODELS_CACHE.mkdir(parents=True, exist_ok=True)
    return SHARED_MODELS_CACHE

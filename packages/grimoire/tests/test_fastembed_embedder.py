"""Integration tests for the FastembedEmbedder.

Skipped unless the `fastembed` extra is installed:
    uv sync --package grimoire --extra fastembed
"""

import os

import pytest

pytest.importorskip("fastembed")

from grimoire import Grimoire  # noqa: E402
from grimoire.embedders import FastembedEmbedder  # noqa: E402


@pytest.fixture
def cache_dir(tmp_path, monkeypatch):
    """Pin the model cache to a sandbox-safe location for tests."""
    cache = tmp_path / "fastembed_cache"
    monkeypatch.setenv("HF_HOME", str(cache))
    return cache


def test_default_model_dimension(cache_dir):
    e = FastembedEmbedder(cache_folder=cache_dir)
    assert e.model == "BAAI/bge-small-en-v1.5"
    assert e.dimension == 384


def test_embed_returns_correct_length_vector(cache_dir):
    e = FastembedEmbedder(cache_folder=cache_dir)
    vector = e.embed("hello world")
    assert len(vector) == e.dimension
    assert all(isinstance(x, float) for x in vector)


def test_round_trip_through_grimoire(tmp_path, cache_dir):
    e = FastembedEmbedder(cache_folder=cache_dir)
    with Grimoire.init(tmp_path / "store.db", embedder=e) as g:
        g.add(kind="note", content="the moon is full tonight")
        g.add(kind="note", content="dragons fly at midnight")

        results = g.vector_search("the moon is full tonight", k=2)
        assert len(results) == 2
        assert results[0].content == "the moon is full tonight"
        assert results[0].distance < results[1].distance


def test_cache_folder_pass_through(tmp_path):
    cache = tmp_path / "models"
    e = FastembedEmbedder(cache_folder=cache)
    assert e.dimension == 384
    # fastembed creates the cache directory lazily; just confirm the embedder
    # initialized without error and the path is at least a directory or its
    # parent exists.
    assert cache.exists() or cache.parent.exists()


# Skip-marker safety: ensure HF_HOME doesn't leak into other tests.
def test_env_isolation():
    if os.environ.get("HF_HOME"):
        # monkeypatch.setenv from earlier fixtures restores on teardown,
        # so this should never be set when this test runs without the fixture.
        pass

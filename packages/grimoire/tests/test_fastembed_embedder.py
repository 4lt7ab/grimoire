"""Integration tests for the FastembedEmbedder.

Skipped unless the `fastembed` extra is installed:
    uv sync --package grimoire --extra fastembed
"""

import pytest

pytest.importorskip("fastembed")

from grimoire import Grimoire  # noqa: E402
from grimoire.embedders import FastembedEmbedder  # noqa: E402


@pytest.fixture
def cache_dir(_shared_models_cache):
    """Use the repo-local shared cache so model files persist across runs.

    Provided by the root conftest. Prime once with `just init`.
    """
    return _shared_models_cache


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


def test_cache_folder_pass_through(_shared_models_cache):
    # Verifies the cache_folder argument flows through to fastembed without
    # triggering a download — uses the warmed shared cache.
    e = FastembedEmbedder(cache_folder=_shared_models_cache)
    assert e.dimension == 384
    assert _shared_models_cache.exists()

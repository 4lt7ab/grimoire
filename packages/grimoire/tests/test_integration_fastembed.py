"""Opt-in integration tests against the real FastembedEmbedder.

Marked `integration` and deselected by default (see the root pyproject's
addopts). Run with `just test-integration` or `uv run pytest -m integration`.
The first run fetches the model weights (~30 MB) from HuggingFace; subsequent
runs reuse the cache and stay offline.

Unlike the fake-embedder suite — which returns zero vectors and so can only
prove the embed→store→search plumbing — these tests assert that real
embeddings produce a *meaningful* distance ordering.
"""

import pytest

from grimoire.data.entry import Entry
from grimoire.embed import FastembedEmbedder
from grimoire.grimoire import Grimoire

pytestmark = pytest.mark.integration


def test_lock_matches_default_model(tmp_path):
    embedder = FastembedEmbedder()
    with Grimoire.open(tmp_path / "g.db", embedder=embedder) as g:
        assert g.embedder is embedder

    peek = Grimoire.peek(tmp_path / "g.db")
    assert peek.model == "BAAI/bge-small-en-v1.5"
    assert peek.dimension == 384 == embedder.dimension


def test_semantic_ranking_is_meaningful(tmp_path):
    """A related query must rank the on-topic entry above an unrelated one."""
    with Grimoire.open(tmp_path / "g.db", embedder=FastembedEmbedder()) as g:
        [bird] = g.add([Entry(None, {"topic": "phoenix"})])
        [taxes] = g.add([Entry(None, {"topic": "taxes"})])
        g.index(bird.uniq_id, search="A solar phoenix reborn from its ashes at dawn")
        g.index(taxes.uniq_id, search="A quarterly report on corporate tax liabilities")

        entries, hits = g.search("mythical bird that rises from fire")

    assert entries[0].uniq_id == bird.uniq_id
    by_id = {h.uniq_id: h.distance for h in hits}
    assert by_id[bird.uniq_id] < by_id[taxes.uniq_id]

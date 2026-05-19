"""Lightweight smoke tests for the vec0 plumbing.

The partition-related coverage that lived here was removed when partitions
were dropped from the schema. What remains is end-to-end exercise of the
embed → store → query path through the new `search` surface.
"""

from grimoire.data.entry import Entry
from grimoire.grimoire import Grimoire


def test_search_on_empty_db_returns_empty(tmp_path, fake_embedder):
    g = Grimoire.open(tmp_path / "g.db", embedder=fake_embedder)
    entries, hits = g.search("anything")
    assert entries == []
    assert hits == []


def test_search_finds_indexed_entry(tmp_path, fake_embedder):
    g = Grimoire.open(tmp_path / "g.db", embedder=fake_embedder)
    [e] = g.add([Entry(None, {"k": "v"})])
    g.index(e.uniq_id, search="the moon glows")

    entries, hits = g.search("moon")
    assert [x.uniq_id for x in entries] == [e.uniq_id]
    assert hits[0].distance >= 0


def test_search_respects_limit(tmp_path, fake_embedder):
    g = Grimoire.open(tmp_path / "g.db", embedder=fake_embedder)
    saved = g.add([Entry(None, None) for _ in range(15)])
    for s in saved:
        g.index(s.uniq_id, search=f"text {s.uniq_id}")

    entries, _ = g.search("query", limit=5)
    assert len(entries) == 5

import pytest
from grimoire.data.entry import Entry
from grimoire.errors import EmbedderRequired, GrimoireMismatch
from grimoire.grimoire import Grimoire


def test_open_fresh_file_without_embedder_locks_to_noop(tmp_path):
    db = tmp_path / "g.db"
    with Grimoire.open(db) as g:
        assert g.embedder is None

    peeked = Grimoire.peek(db)
    assert peeked.model == "noop"
    assert peeked.dimension == 1


def test_open_existing_file_without_embedder_skips_lock_validation(
    tmp_path, fake_embedder
):
    db = tmp_path / "g.db"
    with Grimoire.open(db, embedder=fake_embedder) as g:
        g.add([Entry(None, "tale", None, {"k": "v"})])

    with Grimoire.open(db) as g:
        assert g.embedder is None
        assert len(g.fetch()) == 1


def test_open_fresh_locks_real_embedder_then_reopen_without_embedder_works(
    tmp_path, fake_embedder
):
    db = tmp_path / "g.db"
    Grimoire.open(db, embedder=fake_embedder).__exit__(None, None, None)

    with Grimoire.open(db) as g:
        assert g.embedder is None


def test_noop_locked_file_rejects_real_embedder_on_reopen(tmp_path, fake_embedder):
    db = tmp_path / "g.db"
    Grimoire.open(db).__exit__(None, None, None)

    with pytest.raises(GrimoireMismatch):
        Grimoire.open(db, embedder=fake_embedder)


def test_embed_without_embedder_raises(tmp_path):
    with Grimoire.open(tmp_path / "g.db") as g:
        [saved] = g.add([Entry(None, None, None, None)])
        with pytest.raises(EmbedderRequired):
            g.embed([(saved.id, "anything")])


def test_semantic_search_without_embedder_raises(tmp_path):
    with Grimoire.open(tmp_path / "g.db") as g:
        with pytest.raises(EmbedderRequired):
            g.semantic_search("anything")


def test_embed_empty_without_embedder_is_noop(tmp_path):
    with Grimoire.open(tmp_path / "g.db") as g:
        assert g.embed([]) == []


def test_keyword_path_works_without_embedder(tmp_path):
    with Grimoire.open(tmp_path / "g.db") as g:
        [saved] = g.add([Entry(None, "tale", "moon", {"k": "v"})])
        g.keyword([(saved.id, "moon glow")])

        hits = g.keyword_search("moon")
        assert [h.entry.id for h in hits] == [saved.id]

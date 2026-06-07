import sqlite3

import pytest
from grimoire.data.entry import Entry
from grimoire.errors import EmbedderRequired, GrimoireMismatch, SchemaVersionError
from grimoire.grimoire import Grimoire


def test_open_fresh_file_without_embedder_locks_to_noop(tmp_path):
    db = tmp_path / "g.db"
    with Grimoire.open(db) as g:
        assert g.embedder is None

    peeked = Grimoire.peek(db)
    assert peeked.model == "noop"
    assert peeked.dimension == 1


def test_open_existing_file_without_embedder_skips_validation(tmp_path, fake_embedder):
    db = tmp_path / "g.db"
    with Grimoire.open(db, embedder=fake_embedder) as g:
        g.add([Entry(None, {"k": "v"})])

    with Grimoire.open(db) as g:
        assert g.embedder is None
        # entry table populated; no index added so query is empty
        entries, _ = g.query()
        assert entries == []


def test_open_fresh_locks_real_then_reopen_without_embedder_works(
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


def test_index_search_without_embedder_raises(tmp_path):
    with Grimoire.open(tmp_path / "g.db") as g:
        [e] = g.add([Entry(None, None)])
        with pytest.raises(EmbedderRequired):
            g.index(e.uniq_id, search="anything")


def test_search_without_embedder_raises(tmp_path):
    with Grimoire.open(tmp_path / "g.db") as g, pytest.raises(EmbedderRequired):
        g.search("anything")


def test_index_match_works_without_embedder(tmp_path):
    with Grimoire.open(tmp_path / "g.db") as g:
        [e] = g.add([Entry(None, None)])
        g.index(e.uniq_id, ref="r", match="phoenix")

        entries, _ = g.match("phoenix")
        assert [x.uniq_id for x in entries] == [e.uniq_id]


def test_index_idx_only_works_without_embedder(tmp_path):
    with Grimoire.open(tmp_path / "g.db") as g:
        [e] = g.add([Entry(None, None)])
        g.index(e.uniq_id, ref="r", ord=(1.0, 2.0, 3.0, "a", "b"))

        _, indexes = g.query()
        assert indexes[0].uniq_ref == "r"


def _stamp_version(path, version):
    bare = sqlite3.connect(path)
    bare.execute(f"PRAGMA user_version = {version}")
    bare.commit()
    bare.close()


def test_reopen_with_mismatched_schema_version_raises(tmp_path, fake_embedder):
    db = tmp_path / "g.db"
    Grimoire.open(db, embedder=fake_embedder).__exit__(None, None, None)
    _stamp_version(db, 2)

    with pytest.raises(SchemaVersionError):
        Grimoire.open(db, embedder=fake_embedder)


def test_peek_with_mismatched_schema_version_raises(tmp_path, fake_embedder):
    db = tmp_path / "g.db"
    Grimoire.open(db, embedder=fake_embedder).__exit__(None, None, None)
    _stamp_version(db, 2)

    with pytest.raises(SchemaVersionError):
        Grimoire.peek(db)

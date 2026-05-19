import pytest
from grimoire.data.entry import Entry
from grimoire.errors import GrimoireNotFound
from grimoire.grimoire import Grimoire


def test_peek_missing_file_raises(tmp_path):
    with pytest.raises(GrimoireNotFound):
        Grimoire.peek(tmp_path / "absent.db")


def test_peek_uninitialized_file_raises(tmp_path):
    stub = tmp_path / "empty.db"
    stub.touch()
    with pytest.raises(GrimoireNotFound):
        Grimoire.peek(stub)


def test_peek_reports_lock_and_per_table_counts(tmp_path, fake_embedder):
    db = tmp_path / "g.db"
    with Grimoire.open(db, embedder=fake_embedder) as g:
        a, b, c = g.add([Entry(None, None) for _ in range(3)])
        g.index(a.uniq_id, ref="r", match="text")
        g.index(b.uniq_id, search="text")
        # c has no sidecars

    info = Grimoire.peek(db)
    assert info.model == fake_embedder.model
    assert info.dimension == fake_embedder.dimension
    assert info.schema_version == 1
    assert info.entry_count == 3
    assert info.entry_idx_count == 1
    assert info.entry_fts_count == 1
    assert info.entry_vec_count == 1


def test_peek_empty_database_has_zero_counts(tmp_path, fake_embedder):
    db = tmp_path / "g.db"
    with Grimoire.open(db, embedder=fake_embedder):
        pass

    info = Grimoire.peek(db)
    assert info.entry_count == 0
    assert info.entry_idx_count == 0
    assert info.entry_fts_count == 0
    assert info.entry_vec_count == 0


def test_peek_after_delete_reflects_trigger_cleanup(tmp_path, fake_embedder):
    db = tmp_path / "g.db"
    with Grimoire.open(db, embedder=fake_embedder) as g:
        [e] = g.add([Entry(None, None)])
        g.index(e.uniq_id, ref="r", match="t", search="t")
        g.remove([e.uniq_id])

    info = Grimoire.peek(db)
    assert info.entry_count == 0
    assert info.entry_idx_count == 0
    assert info.entry_fts_count == 0
    assert info.entry_vec_count == 0

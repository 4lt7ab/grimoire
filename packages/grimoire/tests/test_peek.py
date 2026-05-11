import pytest
from grimoire.data.entry import Entry
from grimoire.errors import GrimoireNotFound
from grimoire.grimoire import open as open_grimoire
from grimoire.grimoire import peek


def test_peek_missing_file_raises(tmp_path):
    with pytest.raises(GrimoireNotFound):
        peek(tmp_path / "absent.db")


def test_peek_uninitialized_file_raises(tmp_path):
    stub = tmp_path / "empty.db"
    stub.touch()
    with pytest.raises(GrimoireNotFound):
        peek(stub)


def test_peek_reports_lock_and_counts(tmp_path, fake_embedder):
    db = tmp_path / "g.db"
    g = open_grimoire(db, embedder=fake_embedder)
    g.add(
        [
            Entry(None, "spell", None, None),
            Entry(None, "spell", None, None),
            Entry(None, "item", None, None),
            Entry(None, None, None, None),
        ]
    )
    g._conn.commit()
    g._conn.close()

    info = peek(db)
    assert info.model == fake_embedder.model
    assert info.dimension == fake_embedder.dimension
    assert info.schema_version == 1
    assert info.entry_count == 4
    assert info.group_counts == {"item": 1, "spell": 2, None: 1}

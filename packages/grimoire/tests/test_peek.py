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


def test_peek_reports_lock_and_counts(tmp_path, fake_embedder):
    db = tmp_path / "g.db"
    g = Grimoire.open(db, embedder=fake_embedder)
    saved = g.add(
        [
            Entry(None, "spell", None, None),
            Entry(None, "spell", None, None),
            Entry(None, "item", None, None),
            Entry(None, None, None, None),
        ]
    )
    g.embed([(saved[0].id, "a"), (saved[1].id, "b")], partition="alpha")
    g.embed([(saved[2].id, "c")], partition="beta")
    # saved[3] is unembedded
    g._conn.commit()
    g._conn.close()

    info = Grimoire.peek(db)
    assert info.model == fake_embedder.model
    assert info.dimension == fake_embedder.dimension
    assert info.schema_version == 1
    assert info.entry_count == 4
    assert info.group_counts == {"item": 1, "spell": 2, None: 1}
    assert info.partition_counts == {"alpha": 2, "beta": 1}

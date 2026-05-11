from grimoire.data.entry import Entry
from grimoire.grimoire import open as open_grimoire


def _has_vec_row(conn, entry_id: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM entry_vec WHERE id = ?",
        (entry_id,),
    ).fetchone()
    return row is not None


def test_add_writes_vec_row(tmp_path, fake_embedder):
    g = open_grimoire(tmp_path / "g.db", embedder=fake_embedder)
    [saved] = g.add([Entry(None, None, None, None, semantic_text="hello")])
    assert _has_vec_row(g._conn, saved.id)


def test_add_without_semantic_text_skips_vec(tmp_path, fake_embedder):
    g = open_grimoire(tmp_path / "g.db", embedder=fake_embedder)
    [saved] = g.add([Entry(None, None, None, {"only": "payload"})])
    assert not _has_vec_row(g._conn, saved.id)


def test_add_mixed_indexes_only_opted_in(tmp_path, fake_embedder):
    g = open_grimoire(tmp_path / "g.db", embedder=fake_embedder)
    saved = g.add(
        [
            Entry(None, None, None, None, semantic_text="indexed"),
            Entry(None, None, None, {"only": "payload"}),
            Entry(None, None, None, None, semantic_text="also indexed"),
        ]
    )
    vec_count = g._conn.execute("SELECT COUNT(*) FROM entry_vec").fetchone()[0]
    assert vec_count == 2
    assert _has_vec_row(g._conn, saved[0].id)
    assert not _has_vec_row(g._conn, saved[1].id)
    assert _has_vec_row(g._conn, saved[2].id)


def test_add_batches_embed_calls(tmp_path, fake_embedder):
    g = open_grimoire(tmp_path / "g.db", embedder=fake_embedder)
    g.add(
        [
            Entry(None, None, None, None, semantic_text=f"text {i}")
            for i in range(10)
        ]
    )
    assert fake_embedder.embed_many_calls == 1


def test_add_empty_does_not_call_embedder(tmp_path, fake_embedder):
    g = open_grimoire(tmp_path / "g.db", embedder=fake_embedder)
    assert g.add([]) == []
    assert fake_embedder.embed_many_calls == 0


def test_add_payload_only_does_not_call_embedder(tmp_path, fake_embedder):
    g = open_grimoire(tmp_path / "g.db", embedder=fake_embedder)
    g.add([Entry(None, None, None, {"k": "v"})])
    assert fake_embedder.embed_many_calls == 0


def test_semantic_search_takes_string_query(tmp_path, fake_embedder):
    g = open_grimoire(tmp_path / "g.db", embedder=fake_embedder)
    [saved] = g.add([Entry(None, None, None, None, semantic_text="hello")])
    hits = g.semantic_search("hello", group_key=None)
    assert len(hits) == 1
    assert hits[0].entry.id == saved.id


def test_semantic_search_uses_embed_not_embed_many(tmp_path, fake_embedder):
    g = open_grimoire(tmp_path / "g.db", embedder=fake_embedder)
    g.semantic_search("query", group_key=None)
    assert fake_embedder.embed_calls == 1
    assert fake_embedder.embed_many_calls == 0

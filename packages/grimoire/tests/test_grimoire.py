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
    g.add([Entry(None, None, None, None, semantic_text=f"text {i}") for i in range(10)])
    assert fake_embedder.embed_many_calls == 1


def test_add_empty_does_not_call_embedder(tmp_path, fake_embedder):
    g = open_grimoire(tmp_path / "g.db", embedder=fake_embedder)
    assert g.add([]) == []
    assert fake_embedder.embed_many_calls == 0


def test_add_payload_only_does_not_call_embedder(tmp_path, fake_embedder):
    g = open_grimoire(tmp_path / "g.db", embedder=fake_embedder)
    g.add([Entry(None, None, None, {"k": "v"})])
    assert fake_embedder.embed_many_calls == 0


def test_update_empty_is_noop(tmp_path, fake_embedder):
    g = open_grimoire(tmp_path / "g.db", embedder=fake_embedder)
    assert g.update([]) == []


def test_update_returns_only_existing(tmp_path, fake_embedder):
    g = open_grimoire(tmp_path / "g.db", embedder=fake_embedder)
    [saved] = g.add([Entry(None, None, None, {"a": 1})])

    updated = g.update(
        [
            Entry(saved.id, None, None, {"a": 2}),
            Entry("01MISSINGMISSINGMISSINGMI", None, None, {"a": 3}),
        ]
    )
    assert len(updated) == 1
    assert updated[0].id == saved.id
    assert updated[0].payload == {"a": 2}


def test_update_does_not_call_embedder(tmp_path, fake_embedder):
    g = open_grimoire(tmp_path / "g.db", embedder=fake_embedder)
    [saved] = g.add([Entry(None, None, None, None, semantic_text="hello")])
    fake_embedder.embed_calls = 0
    fake_embedder.embed_many_calls = 0

    g.update([Entry(saved.id, None, None, {"new": "payload"})])
    assert fake_embedder.embed_calls == 0
    assert fake_embedder.embed_many_calls == 0


def test_update_ignores_immutable_fields(tmp_path, fake_embedder):
    g = open_grimoire(tmp_path / "g.db", embedder=fake_embedder)
    [saved] = g.add(
        [
            Entry(
                None,
                group_key="g1",
                group_ref=None,
                payload={"a": 1},
                keyword_text="orig-kw",
                semantic_text="orig-sem",
            )
        ]
    )

    [updated] = g.update(
        [
            Entry(
                saved.id,
                group_key="g2",
                group_ref="ref-1",
                payload={"a": 2},
                keyword_text="new-kw",
                semantic_text="new-sem",
            )
        ]
    )
    assert updated.group_key == "g1"
    assert updated.keyword_text == "orig-kw"
    assert updated.semantic_text == "orig-sem"
    assert updated.group_ref == "ref-1"
    assert updated.payload == {"a": 2}


def test_update_clears_nullable_fields(tmp_path, fake_embedder):
    g = open_grimoire(tmp_path / "g.db", embedder=fake_embedder)
    [saved] = g.add(
        [
            Entry(
                None,
                group_key=None,
                group_ref="ref-1",
                payload={"a": 1},
                context="some context",
                threshold_rank=0.25,
                threshold_distance=0.75,
            )
        ]
    )

    [cleared] = g.update([Entry(saved.id, None, None, None)])
    assert cleared.group_ref is None
    assert cleared.payload is None
    assert cleared.context is None
    assert cleared.threshold_rank is None
    assert cleared.threshold_distance is None


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

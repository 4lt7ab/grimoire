import pytest

from grimoire.data.entry import Entry
from grimoire.grimoire import open as open_grimoire


def _has_vec_row(conn, entry_id: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM entry_vec WHERE id = ?",
        (entry_id,),
    ).fetchone()
    return row is not None


def _vec_partition(conn, entry_id: str) -> str | None:
    return conn.execute(
        "SELECT partition FROM entry_vec WHERE id = ?",
        (entry_id,),
    ).fetchone()["partition"]


def _vec_semantic_text(conn, entry_id: str) -> str | None:
    return conn.execute(
        "SELECT semantic_text FROM entry_vec WHERE id = ?",
        (entry_id,),
    ).fetchone()["semantic_text"]


def test_add_does_not_call_embedder(tmp_path, fake_embedder):
    g = open_grimoire(tmp_path / "g.db", embedder=fake_embedder)
    g.add(
        [
            Entry(None, None, None, None),
            Entry(None, None, None, {"only": "payload"}),
        ]
    )
    assert fake_embedder.embed_calls == 0
    assert fake_embedder.embed_many_calls == 0


def test_add_does_not_write_vec_rows(tmp_path, fake_embedder):
    g = open_grimoire(tmp_path / "g.db", embedder=fake_embedder)
    [saved] = g.add([Entry(None, None, None, None)])
    assert not _has_vec_row(g._conn, saved.id)


def test_add_empty_does_not_call_embedder(tmp_path, fake_embedder):
    g = open_grimoire(tmp_path / "g.db", embedder=fake_embedder)
    assert g.add([]) == []
    assert fake_embedder.embed_many_calls == 0


def test_embed_empty_is_noop(tmp_path, fake_embedder):
    g = open_grimoire(tmp_path / "g.db", embedder=fake_embedder)
    assert g.embed([]) == []
    assert fake_embedder.embed_many_calls == 0


def test_embed_none_is_noop(tmp_path, fake_embedder):
    g = open_grimoire(tmp_path / "g.db", embedder=fake_embedder)
    assert g.embed() == []
    assert fake_embedder.embed_many_calls == 0


def test_embed_writes_vec_row(tmp_path, fake_embedder):
    g = open_grimoire(tmp_path / "g.db", embedder=fake_embedder)
    [saved] = g.add([Entry(None, None, None, None)])

    g.embed([(saved.id, "hello")])
    assert _has_vec_row(g._conn, saved.id)
    assert _vec_semantic_text(g._conn, saved.id) == "hello"


def test_embed_writes_to_partition(tmp_path, fake_embedder):
    g = open_grimoire(tmp_path / "g.db", embedder=fake_embedder)
    [saved] = g.add([Entry(None, None, None, None)])

    g.embed([(saved.id, "hello")], partition="alpha")
    assert _vec_partition(g._conn, saved.id) == "alpha"


def test_embed_replaces_existing_vec_row_in_new_partition(tmp_path, fake_embedder):
    g = open_grimoire(tmp_path / "g.db", embedder=fake_embedder)
    [saved] = g.add([Entry(None, None, None, None)])

    g.embed([(saved.id, "hello")], partition="alpha")
    g.embed([(saved.id, "hello")], partition="beta")

    assert _vec_partition(g._conn, saved.id) == "beta"
    new_hits = g.semantic_search("hello", partition="beta")
    assert [h.entry.id for h in new_hits] == [saved.id]
    old_hits = g.semantic_search("hello", partition="alpha")
    assert old_hits == []


def test_embed_replaces_text_on_reembed(tmp_path, fake_embedder):
    g = open_grimoire(tmp_path / "g.db", embedder=fake_embedder)
    [saved] = g.add([Entry(None, None, None, None)])

    g.embed([(saved.id, "first")])
    g.embed([(saved.id, "second")])

    assert _vec_semantic_text(g._conn, saved.id) == "second"


def test_embed_batches_embed_many(tmp_path, fake_embedder):
    g = open_grimoire(tmp_path / "g.db", embedder=fake_embedder)
    saved = g.add([Entry(None, None, None, None) for _ in range(10)])

    fake_embedder.embed_many_calls = 0
    g.embed([(s.id, f"text {i}") for i, s in enumerate(saved)])
    assert fake_embedder.embed_many_calls == 1


def test_embed_returns_entries(tmp_path, fake_embedder):
    g = open_grimoire(tmp_path / "g.db", embedder=fake_embedder)
    [saved] = g.add([Entry(None, "g1", None, {"k": "v"})])

    [embedded] = g.embed([(saved.id, "hello")])
    assert embedded.id == saved.id
    assert embedded.group_key == "g1"
    assert embedded.payload == {"k": "v"}


def test_embed_raises_for_unknown_id(tmp_path, fake_embedder):
    g = open_grimoire(tmp_path / "g.db", embedder=fake_embedder)
    with pytest.raises(ValueError, match="No entry with id"):
        g.embed([("01MISSINGMISSINGMISSINGMI", "hello")])


def test_keyword_empty_is_noop(tmp_path, fake_embedder):
    g = open_grimoire(tmp_path / "g.db", embedder=fake_embedder)
    assert g.keyword([]) == []


def test_keyword_none_is_noop(tmp_path, fake_embedder):
    g = open_grimoire(tmp_path / "g.db", embedder=fake_embedder)
    assert g.keyword() == []


def test_keyword_indexes_for_match(tmp_path, fake_embedder):
    g = open_grimoire(tmp_path / "g.db", embedder=fake_embedder)
    [saved] = g.add([Entry(None, None, None, None)])

    g.keyword([(saved.id, "the moon glows brightly")])

    hits = g.keyword_search("moon")
    assert [h.entry.id for h in hits] == [saved.id]
    assert hits[0].keyword_text == "the moon glows brightly"


def test_keyword_replaces_text_on_reindex(tmp_path, fake_embedder):
    g = open_grimoire(tmp_path / "g.db", embedder=fake_embedder)
    [saved] = g.add([Entry(None, None, None, None)])

    g.keyword([(saved.id, "moon")])
    g.keyword([(saved.id, "stars")])

    moon_hits = g.keyword_search("moon")
    star_hits = g.keyword_search("stars")
    assert moon_hits == []
    assert [h.entry.id for h in star_hits] == [saved.id]


def test_keyword_raises_for_unknown_id(tmp_path, fake_embedder):
    g = open_grimoire(tmp_path / "g.db", embedder=fake_embedder)
    with pytest.raises(ValueError, match="No entry with id"):
        g.keyword([("01MISSINGMISSINGMISSINGMI", "hello")])


def test_keyword_stores_threshold_rank(tmp_path, fake_embedder):
    g = open_grimoire(tmp_path / "g.db", embedder=fake_embedder)
    [saved] = g.add([Entry(None, None, None, None)])

    g.keyword([(saved.id, "hello")], threshold_rank=0.25)

    hits = g.keyword_search("hello")
    assert [h.threshold_rank for h in hits] == [0.25]


def test_keyword_remove_deletes_fts_row(tmp_path, fake_embedder):
    g = open_grimoire(tmp_path / "g.db", embedder=fake_embedder)
    [saved] = g.add([Entry(None, None, None, None)])
    g.keyword([(saved.id, "hello")])
    assert g.keyword_search("hello") != []

    removed = g.keyword_remove([saved.id])
    assert removed == [saved.id]
    assert g.keyword_search("hello") == []


def test_keyword_remove_missing_id_returns_empty(tmp_path, fake_embedder):
    g = open_grimoire(tmp_path / "g.db", embedder=fake_embedder)
    assert g.keyword_remove(["01MISSINGMISSINGMISSINGMI"]) == []


def test_keyword_remove_leaves_entry_intact(tmp_path, fake_embedder):
    g = open_grimoire(tmp_path / "g.db", embedder=fake_embedder)
    [saved] = g.add([Entry(None, "tale", None, {"k": "v"})])
    g.keyword([(saved.id, "hello")])

    g.keyword_remove([saved.id])

    [entry_after] = g.fetch()
    assert entry_after.id == saved.id
    assert entry_after.group_key == "tale"
    assert entry_after.payload == {"k": "v"}


def test_embed_remove_deletes_vec_row(tmp_path, fake_embedder):
    g = open_grimoire(tmp_path / "g.db", embedder=fake_embedder)
    [saved] = g.add([Entry(None, None, None, None)])
    g.embed([(saved.id, "hello")])
    assert _has_vec_row(g._conn, saved.id)

    removed = g.embed_remove([saved.id])
    assert removed == [saved.id]
    assert not _has_vec_row(g._conn, saved.id)


def test_embed_remove_missing_id_returns_empty(tmp_path, fake_embedder):
    g = open_grimoire(tmp_path / "g.db", embedder=fake_embedder)
    assert g.embed_remove(["01MISSINGMISSINGMISSINGMI"]) == []


def test_embed_remove_leaves_entry_intact(tmp_path, fake_embedder):
    g = open_grimoire(tmp_path / "g.db", embedder=fake_embedder)
    [saved] = g.add([Entry(None, "tale", None, {"k": "v"})])
    g.embed([(saved.id, "hello")])

    g.embed_remove([saved.id])

    [entry_after] = g.fetch()
    assert entry_after.id == saved.id
    assert entry_after.group_key == "tale"
    assert entry_after.payload == {"k": "v"}


def test_embed_stores_threshold_distance(tmp_path, fake_embedder):
    g = open_grimoire(tmp_path / "g.db", embedder=fake_embedder)
    [saved] = g.add([Entry(None, None, None, None)])

    g.embed([(saved.id, "hello")], threshold_distance=0.75)

    hits = g.semantic_search("hello")
    assert [h.threshold_distance for h in hits] == [0.75]


def test_remove_cascades_to_fts_and_vec(tmp_path, fake_embedder):
    g = open_grimoire(tmp_path / "g.db", embedder=fake_embedder)
    [saved] = g.add([Entry(None, None, None, None)])
    g.keyword([(saved.id, "hello")])
    g.embed([(saved.id, "hello")])
    assert _has_vec_row(g._conn, saved.id)
    assert g.keyword_search("hello") != []

    g.remove([saved.id])

    assert not _has_vec_row(g._conn, saved.id)
    assert g.keyword_search("hello") == []


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
    [saved] = g.add([Entry(None, None, None, None)])
    fake_embedder.embed_calls = 0
    fake_embedder.embed_many_calls = 0

    g.update([Entry(saved.id, None, None, {"new": "payload"})])
    assert fake_embedder.embed_calls == 0
    assert fake_embedder.embed_many_calls == 0


def test_update_changes_all_metadata_fields(tmp_path, fake_embedder):
    g = open_grimoire(tmp_path / "g.db", embedder=fake_embedder)
    [saved] = g.add(
        [
            Entry(
                None,
                group_key="g1",
                group_ref=None,
                payload={"a": 1},
                context="orig",
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
                context="new",
            )
        ]
    )
    assert updated.group_key == "g2"
    assert updated.group_ref == "ref-1"
    assert updated.payload == {"a": 2}
    assert updated.context == "new"


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
            )
        ]
    )

    [cleared] = g.update([Entry(saved.id, None, None, None)])
    assert cleared.group_ref is None
    assert cleared.payload is None
    assert cleared.context is None


def test_semantic_search_surfaces_semantic_text(tmp_path, fake_embedder):
    g = open_grimoire(tmp_path / "g.db", embedder=fake_embedder)
    [saved] = g.add([Entry(None, None, None, None)])
    g.embed([(saved.id, "the moon glows")])

    hits = g.semantic_search("moon")
    assert len(hits) == 1
    assert hits[0].entry.id == saved.id
    assert hits[0].semantic_text == "the moon glows"


def test_semantic_search_uses_embed_not_embed_many(tmp_path, fake_embedder):
    g = open_grimoire(tmp_path / "g.db", embedder=fake_embedder)
    fake_embedder.embed_calls = 0
    fake_embedder.embed_many_calls = 0
    g.semantic_search("query")
    assert fake_embedder.embed_calls == 1
    assert fake_embedder.embed_many_calls == 0

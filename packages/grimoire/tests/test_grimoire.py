import pytest
from grimoire.data.entry import Entry, Filters
from grimoire.grimoire import Grimoire


# ----------------------------------------------------------------------
# entry CRUD
# ----------------------------------------------------------------------


def test_add_assigns_uniq_id(tmp_path, fake_embedder):
    g = Grimoire.open(tmp_path / "g.db", embedder=fake_embedder)
    [e] = g.add([Entry(uniq_id=None, data={"k": "v"})])
    assert e.uniq_id is not None
    assert e.data == {"k": "v"}


def test_add_empty_returns_empty(tmp_path, fake_embedder):
    g = Grimoire.open(tmp_path / "g.db", embedder=fake_embedder)
    assert g.add([]) == []


@pytest.mark.parametrize(
    "data",
    [
        {"a": 1, "nested": [1, 2]},
        [1, 2, 3],
        "scalar string",
        42,
        3.14,
        True,
        None,
    ],
)
def test_add_round_trips_data_payloads(tmp_path, fake_embedder, data):
    g = Grimoire.open(tmp_path / "g.db", embedder=fake_embedder)
    [e] = g.add([Entry(uniq_id=None, data=data)])
    [fetched] = g.get([e.uniq_id])
    assert fetched.data == data


def test_update_replaces_data(tmp_path, fake_embedder):
    g = Grimoire.open(tmp_path / "g.db", embedder=fake_embedder)
    [e] = g.add([Entry(None, {"a": 1})])
    [updated] = g.update([Entry(e.uniq_id, {"a": 2})])
    assert updated.data == {"a": 2}
    [fetched] = g.get([e.uniq_id])
    assert fetched.data == {"a": 2}


def test_update_unknown_id_silently_skipped(tmp_path, fake_embedder):
    g = Grimoire.open(tmp_path / "g.db", embedder=fake_embedder)
    [e] = g.add([Entry(None, {"a": 1})])
    updated = g.update(
        [
            Entry(e.uniq_id, {"a": 2}),
            Entry("01MISSINGMISSINGMISSINGMI", {"a": 3}),
        ]
    )
    assert len(updated) == 1
    assert updated[0].uniq_id == e.uniq_id


def test_get_returns_only_existing_no_order_guarantee(tmp_path, fake_embedder):
    g = Grimoire.open(tmp_path / "g.db", embedder=fake_embedder)
    [e] = g.add([Entry(None, {"k": "v"})])
    result = g.get([e.uniq_id, "01MISSINGMISSINGMISSINGMI"])
    assert {x.uniq_id for x in result} == {e.uniq_id}


def test_get_empty_returns_empty(tmp_path, fake_embedder):
    g = Grimoire.open(tmp_path / "g.db", embedder=fake_embedder)
    assert g.get([]) == []


def test_remove_cascades_via_trigger(tmp_path, fake_embedder):
    g = Grimoire.open(tmp_path / "g.db", embedder=fake_embedder)
    [e] = g.add([Entry(None, None)])
    g.index(e.uniq_id, ref="r", match="text", search="text")

    g.remove([e.uniq_id])

    assert g._conn.execute("SELECT COUNT(*) FROM entry").fetchone()[0] == 0
    assert g._conn.execute("SELECT COUNT(*) FROM entry_idx").fetchone()[0] == 0
    assert g._conn.execute("SELECT COUNT(*) FROM entry_fts").fetchone()[0] == 0
    assert g._conn.execute("SELECT COUNT(*) FROM entry_vec").fetchone()[0] == 0


def test_remove_returns_only_actually_deleted(tmp_path, fake_embedder):
    g = Grimoire.open(tmp_path / "g.db", embedder=fake_embedder)
    [e] = g.add([Entry(None, None)])
    removed = g.remove([e.uniq_id, "01MISSINGMISSINGMISSINGMI"])
    assert removed == [e.uniq_id]


# ----------------------------------------------------------------------
# index()  — PUT-style sidecar writes
# ----------------------------------------------------------------------


def test_index_writes_all_three_sidecars(tmp_path, fake_embedder):
    g = Grimoire.open(tmp_path / "g.db", embedder=fake_embedder)
    [e] = g.add([Entry(None, None)])
    g.index(
        e.uniq_id,
        ref="X",
        ord=(1.0, 2.0, 3.0, "a", "b"),
        match="kw",
        search="sem",
    )

    _, indexes = g.query()
    assert len(indexes) == 1
    assert indexes[0].uniq_ref == "X"
    assert indexes[0].ordinal_4 == "a"
    assert indexes[0].ordinal_2 == 2.0


def test_index_put_replaces_idx_row(tmp_path, fake_embedder):
    g = Grimoire.open(tmp_path / "g.db", embedder=fake_embedder)
    [e] = g.add([Entry(None, None)])
    g.index(e.uniq_id, ref="X", ord=(1.0, 2.0, 3.0, "a", "b"))
    g.index(e.uniq_id, ref="Y")  # PUT: ord should clear

    _, indexes = g.query()
    assert indexes[0].uniq_ref == "Y"
    assert indexes[0].ordinal_4 is None
    assert indexes[0].ordinal_5 is None
    assert indexes[0].ordinal_1 is None


def test_index_match_only_does_not_touch_idx(tmp_path, fake_embedder):
    g = Grimoire.open(tmp_path / "g.db", embedder=fake_embedder)
    [e] = g.add([Entry(None, None)])
    g.index(e.uniq_id, ref="X", ord=(None, None, None, "a", "b"))
    g.index(e.uniq_id, match="hello")

    _, indexes = g.query()
    assert indexes[0].uniq_ref == "X"
    assert indexes[0].ordinal_4 == "a"


def test_index_replaces_match(tmp_path, fake_embedder):
    g = Grimoire.open(tmp_path / "g.db", embedder=fake_embedder)
    [e] = g.add([Entry(None, None)])
    g.index(e.uniq_id, match="moon")
    g.index(e.uniq_id, match="stars")

    moon, _ = g.match("moon")
    stars, _ = g.match("stars")
    assert moon == []
    assert [x.uniq_id for x in stars] == [e.uniq_id]


def test_index_validates_ord_tuple_length(tmp_path, fake_embedder):
    g = Grimoire.open(tmp_path / "g.db", embedder=fake_embedder)
    [e] = g.add([Entry(None, None)])
    with pytest.raises(ValueError, match="ord"):
        g.index(e.uniq_id, ord=(1.0, 2.0, 3.0, 4.0))  # type: ignore[arg-type]


def test_index_requires_existing_entry(tmp_path, fake_embedder):
    g = Grimoire.open(tmp_path / "g.db", embedder=fake_embedder)
    with pytest.raises(ValueError, match="No entry"):
        g.index("01MISSINGMISSINGMISSINGMI", ref="X")


def test_index_with_no_kwargs_is_noop(tmp_path, fake_embedder):
    g = Grimoire.open(tmp_path / "g.db", embedder=fake_embedder)
    [e] = g.add([Entry(None, None)])
    g.index(e.uniq_id)
    _, indexes = g.query()
    assert indexes == []


def test_index_partial_ord_writes_null_for_omitted(tmp_path, fake_embedder):
    g = Grimoire.open(tmp_path / "g.db", embedder=fake_embedder)
    [e] = g.add([Entry(None, None)])
    g.index(e.uniq_id, ord=(None, None, None, "a", None))

    _, indexes = g.query()
    assert indexes[0].ordinal_4 == "a"
    assert indexes[0].ordinal_5 is None


# ----------------------------------------------------------------------
# query()
# ----------------------------------------------------------------------


def test_query_returns_parallel_entries_indexes(tmp_path, fake_embedder):
    g = Grimoire.open(tmp_path / "g.db", embedder=fake_embedder)
    [a] = g.add([Entry(None, {"x": 1})])
    [b] = g.add([Entry(None, {"x": 2})])
    g.index(a.uniq_id, ord=("alpha", None, None, None, None))
    g.index(b.uniq_id, ord=("beta", None, None, None, None))

    entries, indexes = g.query()
    assert len(entries) == len(indexes) == 2
    for e, i in zip(entries, indexes, strict=True):
        assert e.uniq_id == i.uniq_id


def test_query_excludes_entries_without_idx(tmp_path, fake_embedder):
    g = Grimoire.open(tmp_path / "g.db", embedder=fake_embedder)
    g.add([Entry(None, None)])
    [b] = g.add([Entry(None, None)])
    g.index(b.uniq_id, ref="r")

    entries, _ = g.query()
    assert [e.uniq_id for e in entries] == [b.uniq_id]


def test_query_filters_by_equals(tmp_path, fake_embedder):
    g = Grimoire.open(tmp_path / "g.db", embedder=fake_embedder)
    [a] = g.add([Entry(None, None)])
    [b] = g.add([Entry(None, None)])
    g.index(a.uniq_id, ord=("alpha", None, None, None, None))
    g.index(b.uniq_id, ord=("beta", None, None, None, None))

    _, indexes = g.query(Filters(equals={"ordinal_1": ["alpha"]}))
    assert [i.uniq_id for i in indexes] == [a.uniq_id]


def test_query_filters_by_ordinal_range(tmp_path, fake_embedder):
    g = Grimoire.open(tmp_path / "g.db", embedder=fake_embedder)
    saved = []
    for v in [1.0, 5.0, 10.0]:
        [e] = g.add([Entry(None, None)])
        g.index(e.uniq_id, ord=(v, None, None, None, None))
        saved.append((e.uniq_id, v))

    _, indexes = g.query(
        Filters(gte={"ordinal_1": 2.0}, lte={"ordinal_1": 7.0})
    )
    assert {i.ordinal_1 for i in indexes} == {5.0}


def test_query_filters_by_text_ordinal_range(tmp_path, fake_embedder):
    g = Grimoire.open(tmp_path / "g.db", embedder=fake_embedder)
    for tag in ["alpha", "mike", "zulu"]:
        [e] = g.add([Entry(None, None)])
        g.index(e.uniq_id, ord=(tag, None, None, None, None))

    _, indexes = g.query(
        Filters(gte={"ordinal_1": "b"}, lte={"ordinal_1": "n"})
    )
    assert {i.ordinal_1 for i in indexes} == {"mike"}


def test_query_cursor_paginates_by_uniq_id(tmp_path, fake_embedder):
    g = Grimoire.open(tmp_path / "g.db", embedder=fake_embedder)
    added = []
    for i in range(5):
        [e] = g.add([Entry(None, None)])
        g.index(e.uniq_id, ref=f"r-{i}")
        added.append(e.uniq_id)

    _, page1 = g.query(limit=2)
    ids1 = [i.uniq_id for i in page1]
    assert ids1 == added[:2]

    _, page2 = g.query(limit=2, cursor=ids1[-1])
    assert [i.uniq_id for i in page2] == added[2:4]


def test_query_invalid_filter_column_raises(tmp_path, fake_embedder):
    g = Grimoire.open(tmp_path / "g.db", embedder=fake_embedder)
    with pytest.raises(ValueError, match="equals filter column"):
        g.query(Filters(equals={"bogus": ["x"]}))


def test_query_gte_rejects_non_ordinal_column(tmp_path, fake_embedder):
    g = Grimoire.open(tmp_path / "g.db", embedder=fake_embedder)
    with pytest.raises(ValueError, match="gte filter column"):
        g.query(Filters(gte={"uniq_ref": "x"}))


# ----------------------------------------------------------------------
# fetch()
# ----------------------------------------------------------------------


def test_fetch_returns_entries_matching_uniq_ref(tmp_path, fake_embedder):
    g = Grimoire.open(tmp_path / "g.db", embedder=fake_embedder)
    [a] = g.add([Entry(None, {"k": "a"})])
    [b] = g.add([Entry(None, {"k": "b"})])
    g.index(a.uniq_id, ref="ext-1")
    g.index(b.uniq_id, ref="ext-2")

    entries, indexes = g.fetch(["ext-1"])
    assert [e.uniq_id for e in entries] == [a.uniq_id]
    assert [i.uniq_ref for i in indexes] == ["ext-1"]


def test_uniq_ref_is_sparse_unique(tmp_path, fake_embedder):
    import sqlite3

    g = Grimoire.open(tmp_path / "g.db", embedder=fake_embedder)
    [a] = g.add([Entry(None, None)])
    [b] = g.add([Entry(None, None)])
    g.index(a.uniq_id, ref="shared")

    with pytest.raises(sqlite3.IntegrityError):
        g.index(b.uniq_id, ref="shared")

    # Re-indexing the same entry with the same ref is fine (PUT replaces).
    g.index(a.uniq_id, ref="shared")
    entries, _ = g.fetch(["shared"])
    assert [e.uniq_id for e in entries] == [a.uniq_id]


def test_fetch_excludes_entries_without_idx(tmp_path, fake_embedder):
    g = Grimoire.open(tmp_path / "g.db", embedder=fake_embedder)
    g.add([Entry(None, None)])  # no index
    [b] = g.add([Entry(None, None)])
    g.index(b.uniq_id, ref="r")

    entries, _ = g.fetch(["r"])
    assert [e.uniq_id for e in entries] == [b.uniq_id]


def test_fetch_empty_returns_empty_tuple(tmp_path, fake_embedder):
    g = Grimoire.open(tmp_path / "g.db", embedder=fake_embedder)
    entries, indexes = g.fetch([])
    assert entries == [] and indexes == []


# ----------------------------------------------------------------------
# match()
# ----------------------------------------------------------------------


def test_match_returns_entry_with_score(tmp_path, fake_embedder):
    g = Grimoire.open(tmp_path / "g.db", embedder=fake_embedder)
    [a] = g.add([Entry(None, {"k": "a"})])
    g.index(a.uniq_id, match="phoenix arcane ember")

    entries, hits = g.match("phoenix")
    assert [e.uniq_id for e in entries] == [a.uniq_id]
    assert hits[0].uniq_id == a.uniq_id
    assert hits[0].score > 0


def test_match_filters_via_idx_join(tmp_path, fake_embedder):
    g = Grimoire.open(tmp_path / "g.db", embedder=fake_embedder)
    [a] = g.add([Entry(None, None)])
    [b] = g.add([Entry(None, None)])
    g.index(a.uniq_id, ord=("alpha", None, None, None, None), match="phoenix")
    g.index(b.uniq_id, ord=("beta", None, None, None, None), match="phoenix")

    entries, _ = g.match(
        "phoenix", filters=Filters(equals={"ordinal_1": ["alpha"]})
    )
    assert [e.uniq_id for e in entries] == [a.uniq_id]


def test_match_orders_by_bm25_desc(tmp_path, fake_embedder):
    g = Grimoire.open(tmp_path / "g.db", embedder=fake_embedder)
    [a] = g.add([Entry(None, None)])
    [b] = g.add([Entry(None, None)])
    g.index(a.uniq_id, match="phoenix phoenix phoenix")
    g.index(b.uniq_id, match="phoenix")

    _, hits = g.match("phoenix")
    scores = [h.score for h in hits]
    assert scores == sorted(scores, reverse=True)
    assert all(s >= 0 for s in scores)


def test_match_rejects_empty_query(tmp_path, fake_embedder):
    g = Grimoire.open(tmp_path / "g.db", embedder=fake_embedder)
    with pytest.raises(ValueError, match="non-empty"):
        g.match("")


# ----------------------------------------------------------------------
# search()
# ----------------------------------------------------------------------


def test_search_returns_entry_with_distance(tmp_path, fake_embedder):
    g = Grimoire.open(tmp_path / "g.db", embedder=fake_embedder)
    [a] = g.add([Entry(None, {"k": "v"})])
    g.index(a.uniq_id, search="phoenix")

    entries, hits = g.search("phoenix")
    assert [e.uniq_id for e in entries] == [a.uniq_id]
    assert hits[0].uniq_id == a.uniq_id
    assert hits[0].distance >= 0


def test_search_orders_by_distance_asc(tmp_path, fake_embedder):
    g = Grimoire.open(tmp_path / "g.db", embedder=fake_embedder)
    saved = g.add([Entry(None, None) for _ in range(3)])
    for s in saved:
        g.index(s.uniq_id, search="text")

    _, hits = g.search("anything")
    distances = [h.distance for h in hits]
    assert distances == sorted(distances)


def test_search_empty_returns_empty(tmp_path, fake_embedder):
    g = Grimoire.open(tmp_path / "g.db", embedder=fake_embedder)
    entries, hits = g.search("anything")
    assert entries == [] and hits == []


def test_search_uses_embed_not_embed_many(tmp_path, fake_embedder):
    g = Grimoire.open(tmp_path / "g.db", embedder=fake_embedder)
    fake_embedder.embed_calls = 0
    fake_embedder.embed_many_calls = 0
    g.search("query")
    assert fake_embedder.embed_calls == 1
    assert fake_embedder.embed_many_calls == 0


# ----------------------------------------------------------------------
# Cascade-by-trigger details
# ----------------------------------------------------------------------


def test_remove_cleans_match_so_search_is_empty(tmp_path, fake_embedder):
    g = Grimoire.open(tmp_path / "g.db", embedder=fake_embedder)
    [e] = g.add([Entry(None, None)])
    g.index(e.uniq_id, match="phoenix")
    assert g.match("phoenix")[0] != []

    g.remove([e.uniq_id])
    entries, _ = g.match("phoenix")
    assert entries == []

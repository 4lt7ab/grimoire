import hashlib
import sqlite3
import threading
import time
from datetime import UTC, datetime, timedelta

import pytest
from grimoire import (
    Entry,
    Grimoire,
    GrimoireMismatch,
    GrimoireNotFound,
    InvalidEmbedder,
    Stats,
)
from grimoire.core import _create_file, _open_file
from ulid import ULID


class FakeEmbedder:
    def __init__(self, model: str = "fake-v1", dimension: int = 8) -> None:
        self._model = model
        self._dimension = dimension

    @property
    def model(self) -> str:
        return self._model

    @property
    def dimension(self) -> int:
        return self._dimension

    def embed(self, text: str) -> list[float]:
        digest = hashlib.sha256(text.encode()).digest()
        return [(b - 128) / 128.0 for b in digest[: self._dimension]]

    def embed_many(self, texts: list[str]) -> list[list[float]]:
        return [self.embed(t) for t in texts]


class CountingEmbedder(FakeEmbedder):
    """A FakeEmbedder that records how many times `embed`/`embed_many` is called."""

    def __init__(self, *, model: str = "fake-v1", dimension: int = 8) -> None:
        super().__init__(model=model, dimension=dimension)
        self.embed_calls = 0
        self.embed_many_calls = 0

    def embed(self, text: str) -> list[float]:
        self.embed_calls += 1
        return FakeEmbedder.embed(self, text)

    def embed_many(self, texts: list[str]) -> list[list[float]]:
        self.embed_many_calls += 1
        # Bypass `self.embed` so callers can distinguish a true batch call
        # from N single-record calls via `embed_calls` alone.
        return [FakeEmbedder.embed(self, t) for t in texts]


def test_init_creates_file_idempotently(tmp_path):
    db = tmp_path / "store.db"
    _create_file(db, embedder=FakeEmbedder()).close()
    _create_file(db, embedder=FakeEmbedder()).close()
    assert db.exists()


def test_init_enables_wal_journal_mode(tmp_path):
    db = tmp_path / "store.db"
    _create_file(db, embedder=FakeEmbedder()).close()
    # Verify via a raw connection — the journal mode persists in the db
    # header, so any subsequent open inherits it.
    raw = sqlite3.connect(db)
    try:
        mode = raw.execute("PRAGMA journal_mode").fetchone()[0]
    finally:
        raw.close()
    assert mode.lower() == "wal"


def test_embedder_model_mismatch_raises(tmp_path):
    db = tmp_path / "store.db"
    _create_file(db, embedder=FakeEmbedder(model="alpha")).close()
    with pytest.raises(GrimoireMismatch):
        _open_file(db, embedder=FakeEmbedder(model="beta"))


def test_embedder_dimension_mismatch_raises(tmp_path):
    db = tmp_path / "store.db"
    _create_file(db, embedder=FakeEmbedder(dimension=8)).close()
    with pytest.raises(GrimoireMismatch):
        _open_file(db, embedder=FakeEmbedder(dimension=16))


def test_add_returns_entry(tmp_path):
    with _create_file(tmp_path / "store.db", embedder=FakeEmbedder()) as g:
        entry = g.add(group_key="note", content="the moon is full")
        assert isinstance(entry, Entry)
        assert entry.group_key == "note"
        assert entry.content == "the moon is full"


def test_search_finds_exact_match_first(tmp_path):
    with _create_file(tmp_path / "store.db", embedder=FakeEmbedder()) as g:
        g.add(group_key="note", content="the moon is full")
        g.add(group_key="note", content="dragons fly at midnight")
        g.add(group_key="note", content="potions bubble in the cauldron")

        results = g.vector_search("the moon is full", k=3)
        assert len(results) == 3
        assert results[0].content == "the moon is full"
        assert results[0].distance == 0.0


def test_search_filters_by_group_key(tmp_path):
    with _create_file(tmp_path / "store.db", embedder=FakeEmbedder()) as g:
        g.add(group_key="spell", content="lumos")
        g.add(group_key="potion", content="lumos")

        results = g.vector_search("lumos", group_key="spell", k=10)
        assert len(results) == 1
        assert results[0].group_key == "spell"


def test_dynamic_threshold_drops_low_match(tmp_path):
    with _create_file(tmp_path / "store.db", embedder=FakeEmbedder()) as g:
        g.add(group_key="note", content="the moon is full", threshold=0.0)
        g.add(group_key="note", content="dragons fly at midnight", threshold=0.0)

        all_results = g.vector_search("the moon is full", k=10)
        assert len(all_results) == 2

        gated = g.vector_search("the moon is full", k=10, dynamic_threshold=True)
        assert len(gated) == 1
        assert gated[0].content == "the moon is full"


def test_two_files_are_independent(tmp_path):
    a_path = tmp_path / "a.db"
    b_path = tmp_path / "b.db"
    with _create_file(a_path, embedder=FakeEmbedder()) as a:
        a.add(group_key="note", content="alpha")
    with _create_file(b_path, embedder=FakeEmbedder()) as b:
        b.add(group_key="note", content="beta")
        results = b.vector_search("alpha", k=10)
        assert all(r.content != "alpha" for r in results)


def test_data_persists_across_reopens(tmp_path):
    db = tmp_path / "store.db"
    with _create_file(db, embedder=FakeEmbedder()) as g:
        g.add(group_key="note", content="the moon is full")

    with _open_file(db, embedder=FakeEmbedder()) as g:
        results = g.vector_search("the moon is full", k=1)
        assert len(results) == 1
        assert results[0].content == "the moon is full"


def test_get_returns_entry(tmp_path):
    with _create_file(tmp_path / "store.db", embedder=FakeEmbedder()) as g:
        added = g.add(group_key="note", content="lumos")
        fetched = g.get(added.id)
        assert fetched is not None
        assert fetched.id == added.id
        assert fetched.content == "lumos"


def test_get_returns_none_for_missing_id(tmp_path):
    with _create_file(tmp_path / "store.db", embedder=FakeEmbedder()) as g:
        assert g.get("01HXXXXXXXXXXXXXXXXXXXXXXX") is None


def test_list_returns_all_entries_in_chronological_order(tmp_path):
    with _create_file(tmp_path / "store.db", embedder=FakeEmbedder()) as g:
        a = g.add(group_key="note", content="first")
        b = g.add(group_key="note", content="second")
        c = g.add(group_key="note", content="third")
        results = g.list()
        assert [r.id for r in results] == [a.id, b.id, c.id]


def test_list_filters_by_group_key(tmp_path):
    with _create_file(tmp_path / "store.db", embedder=FakeEmbedder()) as g:
        g.add(group_key="spell", content="lumos")
        g.add(group_key="potion", content="felix felicis")
        g.add(group_key="spell", content="alohomora")

        spells = g.list(group_key="spell")
        assert len(spells) == 2
        assert all(r.group_key == "spell" for r in spells)


def test_list_paginates_via_after_id(tmp_path):
    with _create_file(tmp_path / "store.db", embedder=FakeEmbedder()) as g:
        added = [g.add(group_key="note", content=f"e{i}") for i in range(5)]

        page1 = g.list(limit=2)
        assert [r.id for r in page1] == [added[0].id, added[1].id]

        page2 = g.list(limit=2, after_id=page1[-1].id)
        assert [r.id for r in page2] == [added[2].id, added[3].id]

        page3 = g.list(limit=2, after_id=page2[-1].id)
        assert [r.id for r in page3] == [added[4].id]

        page4 = g.list(limit=2, after_id=page3[-1].id)
        assert page4 == []


def test_list_respects_limit(tmp_path):
    with _create_file(tmp_path / "store.db", embedder=FakeEmbedder()) as g:
        for i in range(5):
            g.add(group_key="note", content=f"e{i}")
        assert len(g.list(limit=3)) == 3


def test_delete_removes_entry_and_vector(tmp_path):
    with _create_file(tmp_path / "store.db", embedder=FakeEmbedder()) as g:
        added = g.add(group_key="note", content="ephemeral")
        assert g.delete(added.id) is True
        assert g.get(added.id) is None

        # Search should no longer return it.
        results = g.vector_search("ephemeral", k=10)
        assert all(r.id != added.id for r in results)


def test_delete_returns_false_for_missing_id(tmp_path):
    with _create_file(tmp_path / "store.db", embedder=FakeEmbedder()) as g:
        assert g.delete("01HXXXXXXXXXXXXXXXXXXXXXXX") is False


class _BadDimensionEmbedder:
    @property
    def model(self) -> str:
        return "bad"

    @property
    def dimension(self):  # not annotated, returns whatever
        return "8); DROP TABLE entries; --"

    def embed(self, text: str) -> list[float]:
        return [0.0] * 8


class _NonPositiveDimensionEmbedder:
    @property
    def model(self) -> str:
        return "bad"

    @property
    def dimension(self) -> int:
        return 0

    def embed(self, text: str) -> list[float]:
        return []


class _EmptyModelEmbedder:
    @property
    def model(self) -> str:
        return ""

    @property
    def dimension(self) -> int:
        return 8

    def embed(self, text: str) -> list[float]:
        return [0.0] * 8


def test_embedder_with_non_int_dimension_rejected(tmp_path):
    db = tmp_path / "store.db"
    with pytest.raises(InvalidEmbedder):
        _create_file(db, embedder=_BadDimensionEmbedder())
    assert not db.exists() or db.stat().st_size == 0


def test_embedder_with_zero_dimension_rejected(tmp_path):
    with pytest.raises(InvalidEmbedder):
        _create_file(tmp_path / "store.db", embedder=_NonPositiveDimensionEmbedder())


def test_embedder_with_empty_model_rejected(tmp_path):
    with pytest.raises(InvalidEmbedder):
        _create_file(tmp_path / "store.db", embedder=_EmptyModelEmbedder())


# ---------- init ----------


def test_init_exercises_embedder_exactly_once_on_create(tmp_path):
    e = CountingEmbedder()
    _create_file(tmp_path / "store.db", embedder=e).close()
    assert e.embed_calls == 1


def test_init_exercises_embedder_again_on_reinit(tmp_path):
    db = tmp_path / "store.db"
    e = CountingEmbedder()
    _create_file(db, embedder=e).close()
    _create_file(db, embedder=e).close()
    assert e.embed_calls == 2


def test_init_creates_parent_directories(tmp_path):
    nested = tmp_path / "deep" / "nested" / "store.db"
    _create_file(nested, embedder=FakeEmbedder()).close()
    assert nested.exists()


def test_init_propagates_embedder_errors(tmp_path):
    class BoomEmbedder(FakeEmbedder):
        def embed(self, text: str) -> list[float]:
            raise RuntimeError("model fetch failed")

    with pytest.raises(RuntimeError, match="model fetch failed"):
        _create_file(tmp_path / "store.db", embedder=BoomEmbedder())


def test_init_raises_mismatch_on_lock_conflict(tmp_path):
    db = tmp_path / "store.db"
    _create_file(db, embedder=FakeEmbedder(model="alpha")).close()
    with pytest.raises(GrimoireMismatch):
        _create_file(db, embedder=FakeEmbedder(model="beta"))


# ---------- open ----------


def test_open_raises_not_found_for_missing_path(tmp_path):
    with pytest.raises(GrimoireNotFound):
        _open_file(tmp_path / "nope.db", embedder=FakeEmbedder())


def test_open_raises_not_found_for_non_grimoire_file(tmp_path):
    db = tmp_path / "stranger.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE other (x INTEGER)")
    conn.commit()
    conn.close()
    with pytest.raises(GrimoireNotFound):
        _open_file(db, embedder=FakeEmbedder())


def test_open_does_not_call_embed(tmp_path):
    """Regression pin: open() must stay cheap — no embedder.embed() during setup."""
    db = tmp_path / "store.db"
    _create_file(db, embedder=FakeEmbedder()).close()
    e = CountingEmbedder()
    _open_file(db, embedder=e).close()
    assert e.embed_calls == 0


# ---------- peek ----------


def test_peek_returns_none_for_missing_file(tmp_path):
    assert Grimoire.peek(tmp_path / "nope.db") is None


def test_peek_returns_none_for_non_grimoire_file(tmp_path):
    db = tmp_path / "stranger.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE other (x INTEGER)")
    conn.commit()
    conn.close()
    assert Grimoire.peek(db) is None


def test_peek_returns_stats_for_initialized_grimoire(tmp_path):
    db = tmp_path / "store.db"
    with _create_file(db, embedder=FakeEmbedder(model="m1", dimension=8)) as g:
        g.add(group_key="note", content="alpha")
        g.add(group_key="note", content="beta")
        g.add(group_key="spell", content="lumos")

    stats = Grimoire.peek(db)
    assert isinstance(stats, Stats)
    assert stats.model == "m1"
    assert stats.dimension == 8
    assert stats.schema_version == 2
    assert stats.entry_count == 3
    assert stats.groups == {"note": 2, "spell": 1}


# ---------- age-windowed reads ----------


def test_entry_created_at_round_trips_to_ulid_timestamp():
    moment = datetime(2026, 5, 4, 10, 30, 0, tzinfo=UTC)
    entry_id = str(ULID.from_datetime(moment))
    entry = Entry(id=entry_id, group_key="note", content="x")
    assert entry.created_at == moment


def test_list_created_after_is_inclusive_lower_bound(tmp_path):
    with _create_file(tmp_path / "store.db", embedder=FakeEmbedder()) as g:
        a = g.add(group_key="note", content="a")
        time.sleep(0.005)
        b = g.add(group_key="note", content="b")
        time.sleep(0.005)
        c = g.add(group_key="note", content="c")

        results = g.list(created_after=b.created_at)
        assert [r.id for r in results] == [b.id, c.id]
        assert a.id not in [r.id for r in results]


def test_list_created_before_is_exclusive_upper_bound(tmp_path):
    with _create_file(tmp_path / "store.db", embedder=FakeEmbedder()) as g:
        a = g.add(group_key="note", content="a")
        time.sleep(0.005)
        b = g.add(group_key="note", content="b")
        time.sleep(0.005)
        c = g.add(group_key="note", content="c")

        results = g.list(created_before=c.created_at)
        assert [r.id for r in results] == [a.id, b.id]


def test_list_combines_both_bounds(tmp_path):
    with _create_file(tmp_path / "store.db", embedder=FakeEmbedder()) as g:
        g.add(group_key="note", content="a")
        time.sleep(0.005)
        b = g.add(group_key="note", content="b")
        time.sleep(0.005)
        c = g.add(group_key="note", content="c")
        time.sleep(0.005)
        g.add(group_key="note", content="d")

        results = g.list(created_after=b.created_at, created_before=c.created_at)
        assert [r.id for r in results] == [b.id]


def test_search_honors_created_after(tmp_path):
    with _create_file(tmp_path / "store.db", embedder=FakeEmbedder()) as g:
        old = g.add(group_key="note", content="lumos")
        time.sleep(0.005)
        new = g.add(group_key="note", content="lumos")

        results = g.vector_search("lumos", k=10, created_after=new.created_at)
        ids = [r.id for r in results]
        assert new.id in ids
        assert old.id not in ids


def test_search_honors_created_before(tmp_path):
    with _create_file(tmp_path / "store.db", embedder=FakeEmbedder()) as g:
        old = g.add(group_key="note", content="lumos")
        time.sleep(0.005)
        new = g.add(group_key="note", content="lumos")

        results = g.vector_search("lumos", k=10, created_before=new.created_at)
        ids = [r.id for r in results]
        assert old.id in ids
        assert new.id not in ids


def test_list_window_excludes_everything_before_grimoire(tmp_path):
    with _create_file(tmp_path / "store.db", embedder=FakeEmbedder()) as g:
        g.add(group_key="note", content="a")
        g.add(group_key="note", content="b")

        far_future = datetime.now(tz=UTC) + timedelta(days=365)
        assert g.list(created_after=far_future) == []


# ---------- keyword search ----------


def test_keyword_search_finds_exact_token(tmp_path):
    with _create_file(tmp_path / "store.db", embedder=FakeEmbedder()) as g:
        g.add(group_key="note", content="the moon is full")
        g.add(group_key="note", content="dragons fly at midnight")
        g.add(group_key="note", content="potions bubble in the cauldron")

        results = g.keyword_search("moon")
        assert len(results) == 1
        assert results[0].content == "the moon is full"


def test_keyword_search_populates_rank(tmp_path):
    with _create_file(tmp_path / "store.db", embedder=FakeEmbedder()) as g:
        g.add(group_key="note", content="the moon is full")
        results = g.keyword_search("moon")
        assert results[0].rank is not None


def test_keyword_search_filters_by_group_key(tmp_path):
    with _create_file(tmp_path / "store.db", embedder=FakeEmbedder()) as g:
        g.add(group_key="spell", content="lumos lights the way")
        g.add(group_key="potion", content="lumos is also a potion")

        results = g.keyword_search("lumos", group_key="spell")
        assert len(results) == 1
        assert results[0].group_key == "spell"


def test_keyword_search_returns_empty_on_no_match(tmp_path):
    with _create_file(tmp_path / "store.db", embedder=FakeEmbedder()) as g:
        g.add(group_key="note", content="dragons fly at midnight")
        assert g.keyword_search("phoenix") == []


def test_keyword_search_respects_k(tmp_path):
    with _create_file(tmp_path / "store.db", embedder=FakeEmbedder()) as g:
        for i in range(5):
            g.add(group_key="note", content=f"dragon {i}")
        assert len(g.keyword_search("dragon", k=2)) == 2


def test_keyword_search_honors_created_after(tmp_path):
    with _create_file(tmp_path / "store.db", embedder=FakeEmbedder()) as g:
        old = g.add(group_key="note", content="dragon old")
        time.sleep(0.005)
        new = g.add(group_key="note", content="dragon new")
        results = g.keyword_search("dragon", created_after=new.created_at)
        ids = [r.id for r in results]
        assert new.id in ids
        assert old.id not in ids


def test_keyword_search_honors_created_before(tmp_path):
    with _create_file(tmp_path / "store.db", embedder=FakeEmbedder()) as g:
        old = g.add(group_key="note", content="dragon old")
        time.sleep(0.005)
        new = g.add(group_key="note", content="dragon new")
        results = g.keyword_search("dragon", created_before=new.created_at)
        ids = [r.id for r in results]
        assert old.id in ids
        assert new.id not in ids


def test_delete_removes_keyword_index_too(tmp_path):
    with _create_file(tmp_path / "store.db", embedder=FakeEmbedder()) as g:
        added = g.add(group_key="note", content="ephemeral phoenix")
        assert g.delete(added.id) is True
        assert g.keyword_search("phoenix") == []


def test_peek_does_not_require_embedder_or_extension(tmp_path):
    # peek must be safe on a freshly-created file from another process,
    # without sqlite-vec or an embedder loaded.
    db = tmp_path / "store.db"
    _create_file(db, embedder=FakeEmbedder()).close()
    stats = Grimoire.peek(db)
    assert stats is not None
    assert stats.entry_count == 0
    assert stats.groups == {}


# ---------- keywords ----------


def test_add_round_trips_keywords(tmp_path):
    with _create_file(tmp_path / "store.db", embedder=FakeEmbedder()) as g:
        added = g.add(group_key="note", content="hello", keywords=["alpha", "beta"])
        assert added.keywords == ["alpha", "beta"]
        fetched = g.get(added.id)
        assert fetched is not None
        assert fetched.keywords == ["alpha", "beta"]


def test_add_keywords_default_to_none(tmp_path):
    with _create_file(tmp_path / "store.db", embedder=FakeEmbedder()) as g:
        added = g.add(group_key="note", content="hello")
        assert added.keywords is None
        fetched = g.get(added.id)
        assert fetched is not None
        assert fetched.keywords is None


def test_add_empty_keywords_list_stored_as_list(tmp_path):
    # An explicit empty list is distinct from None — preserve caller intent.
    with _create_file(tmp_path / "store.db", embedder=FakeEmbedder()) as g:
        added = g.add(group_key="note", content="hello", keywords=[])
        fetched = g.get(added.id)
        assert fetched is not None
        assert fetched.keywords == []


def test_keyword_search_finds_entry_by_keyword_alone(tmp_path):
    # The token "phoenix" appears only in keywords, not in content.
    with _create_file(tmp_path / "store.db", embedder=FakeEmbedder()) as g:
        g.add(group_key="note", content="A bird sings at dawn", keywords=["phoenix"])
        g.add(group_key="note", content="Dragons fly at midnight")
        results = g.keyword_search("phoenix")
        assert len(results) == 1
        assert results[0].keywords == ["phoenix"]


def test_keyword_search_keyword_match_outranks_content_match(tmp_path):
    # Two entries: one matches the query via keywords, the other via content.
    # With 5x weighting on the keywords column, the keyword match wins.
    with _create_file(tmp_path / "store.db", embedder=FakeEmbedder()) as g:
        content_match = g.add(
            group_key="note",
            content="dawn rises on a quiet field",
            keywords=["morning"],
        )
        keyword_match = g.add(
            group_key="note",
            content="A solar phoenix reborn from ashes",
            keywords=["dawn"],
        )
        results = g.keyword_search("dawn")
        assert {r.id for r in results} == {content_match.id, keyword_match.id}
        # FTS5 bm25() returns negative values; smaller (more negative) is better.
        assert results[0].id == keyword_match.id
        assert results[0].rank < results[1].rank


def test_keyword_search_column_scoped_query_targets_keywords(tmp_path):
    with _create_file(tmp_path / "store.db", embedder=FakeEmbedder()) as g:
        in_content = g.add(group_key="note", content="phoenix in the sky")
        in_keywords = g.add(
            group_key="note", content="A bird sings", keywords=["phoenix"]
        )
        results = g.keyword_search("keywords:phoenix")
        assert {r.id for r in results} == {in_keywords.id}
        assert in_content.id not in {r.id for r in results}


def test_delete_removes_keyword_index_entry_too(tmp_path):
    with _create_file(tmp_path / "store.db", embedder=FakeEmbedder()) as g:
        added = g.add(group_key="note", content="A bird sings", keywords=["phoenix"])
        assert g.delete(added.id) is True
        assert g.keyword_search("phoenix") == []


def test_keywords_returned_by_list_and_search(tmp_path):
    with _create_file(tmp_path / "store.db", embedder=FakeEmbedder()) as g:
        g.add(group_key="note", content="hello", keywords=["alpha"])
        listed = g.list()
        assert listed[0].keywords == ["alpha"]
        # Also via vector_search
        v_results = g.vector_search("hello", k=1)
        assert v_results[0].keywords == ["alpha"]
        # And via keyword_search
        k_results = g.keyword_search("alpha")
        assert k_results[0].keywords == ["alpha"]


# ---------- add_many ----------


def test_add_many_round_trips(tmp_path):
    with _create_file(tmp_path / "store.db", embedder=FakeEmbedder()) as g:
        records = [
            {"group_key": "note", "content": "alpha"},
            {"group_key": "spell", "content": "lumos"},
            {
                "group_key": "note",
                "content": "beta",
                "keywords": ["b"],
                "payload": {"weight": 1},
                "threshold": 0.5,
            },
        ]
        added = g.add_many(records)
        assert [e.content for e in added] == ["alpha", "lumos", "beta"]
        assert [e.group_key for e in added] == ["note", "spell", "note"]
        assert added[2].keywords == ["b"]
        assert added[2].payload == {"weight": 1}
        assert added[2].threshold == 0.5
        # Persisted: each entry is fetchable, searchable.
        for entry in added:
            fetched = g.get(entry.id)
            assert fetched is not None
            assert fetched.content == entry.content


def test_add_many_returns_empty_list_on_empty_input(tmp_path):
    with _create_file(tmp_path / "store.db", embedder=FakeEmbedder()) as g:
        assert g.add_many([]) == []
        assert g.list() == []


def test_add_many_calls_embed_many_once(tmp_path):
    db = tmp_path / "store.db"
    _create_file(db, embedder=CountingEmbedder()).close()

    e = CountingEmbedder()
    with _open_file(db, embedder=e) as g:
        e.embed_calls = 0
        e.embed_many_calls = 0
        records = [{"group_key": "note", "content": f"e{i}"} for i in range(5)]
        g.add_many(records)
        # One batch call, not five single-embed calls.
        assert e.embed_many_calls == 1
        assert e.embed_calls == 0


def test_add_many_atomic_on_embed_failure(tmp_path):
    """If embedding fails, no records leak through partially."""

    class FailEmbedMany(FakeEmbedder):
        def embed_many(self, texts):
            raise RuntimeError("embed batch failed")

    db = tmp_path / "store.db"
    with _create_file(db, embedder=FakeEmbedder()) as g:
        g.add(group_key="note", content="existing")

    with _open_file(db, embedder=FailEmbedMany()) as g:
        with pytest.raises(RuntimeError, match="embed batch failed"):
            g.add_many([{"group_key": "note", "content": "new"}])
        remaining = g.list()
        assert len(remaining) == 1
        assert remaining[0].content == "existing"


def test_add_many_results_are_searchable(tmp_path):
    with _create_file(tmp_path / "store.db", embedder=FakeEmbedder()) as g:
        g.add_many(
            [
                {"group_key": "note", "content": "the moon is full"},
                {"group_key": "note", "content": "dragons fly at midnight"},
            ]
        )
        v_results = g.vector_search("the moon is full", k=2)
        assert v_results[0].content == "the moon is full"
        k_results = g.keyword_search("dragons")
        assert len(k_results) == 1


# ---------- thread sharing ----------


def test_init_default_is_thread_bound(tmp_path):
    """Pin: by default a Grimoire is bound to its constructing thread.

    Single-threaded scripts and the CLI rely on this safety rail. If the
    default ever flips, this test goes loud — making the change deliberate.
    """
    db = tmp_path / "store.db"
    with _create_file(db, embedder=FakeEmbedder()) as g:
        errors: list[Exception] = []

        def worker() -> None:
            try:
                g.add(group_key="note", content="x")
            except sqlite3.ProgrammingError as exc:
                errors.append(exc)

        t = threading.Thread(target=worker)
        t.start()
        t.join()
        assert len(errors) == 1
        assert "thread" in str(errors[0]).lower()


def test_init_check_same_thread_false_allows_worker_thread_use(tmp_path):
    """A Grimoire opened with check_same_thread=False survives a thread hop.

    Canonical case: FastAPI sync handlers run in asyncio's default executor.
    Without this kwarg, the first call from a worker thread raises
    ProgrammingError before any work happens.
    """
    db = tmp_path / "store.db"
    with _create_file(db, embedder=FakeEmbedder(), check_same_thread=False) as g:
        added: list[str] = []

        def worker() -> None:
            entry = g.add(group_key="note", content="hello from worker")
            added.append(entry.id)

        t = threading.Thread(target=worker)
        t.start()
        t.join()
        assert len(added) == 1

        # The write is visible to the main thread, both via id lookup and search.
        fetched = g.get(added[0])
        assert fetched is not None
        assert fetched.content == "hello from worker"
        results = g.vector_search("hello from worker", k=1)
        assert results[0].id == added[0]
        kw = g.keyword_search("worker")
        assert len(kw) == 1


def test_open_check_same_thread_false_threads_through(tmp_path):
    """The kwarg must work on `open` too, not just `init`."""
    db = tmp_path / "store.db"
    _create_file(db, embedder=FakeEmbedder()).close()

    with _open_file(db, embedder=FakeEmbedder(), check_same_thread=False) as g:
        added: list[str] = []

        def worker() -> None:
            added.append(g.add(group_key="note", content="reopened cross-thread").id)

        t = threading.Thread(target=worker)
        t.start()
        t.join()
        assert len(added) == 1
        assert g.get(added[0]) is not None


# ---------- update_many / delete_many ----------


def test_update_many_returns_empty_list_on_empty_input(tmp_path):
    with _create_file(tmp_path / "store.db", embedder=FakeEmbedder()) as g:
        assert g.update_many([]) == []


def test_update_many_returns_entries_aligned_to_input_order(tmp_path):
    with _create_file(tmp_path / "store.db", embedder=FakeEmbedder()) as g:
        a = g.add(group_key="note", content="first")
        b = g.add(group_key="note", content="second")
        c = g.add(group_key="note", content="third")
        results = g.update_many(
            [
                {"id": c.id, "content": "c2"},
                {"id": a.id, "content": "a2"},
                {"id": b.id, "content": "b2"},
            ]
        )
        assert [r.id for r in results] == [c.id, a.id, b.id]
        assert [r.content for r in results] == ["c2", "a2", "b2"]


def test_update_many_returns_none_for_unknown_id(tmp_path):
    with _create_file(tmp_path / "store.db", embedder=FakeEmbedder()) as g:
        a = g.add(group_key="note", content="real")
        results = g.update_many(
            [
                {"id": a.id, "content": "still real"},
                {"id": "01HXXXXXXXXXXXXXXXXXXXXXXX", "content": "ghost"},
            ]
        )
        assert results[0] is not None
        assert results[0].content == "still real"
        assert results[1] is None


def test_update_many_calls_embed_many_once_for_changed_contents(tmp_path):
    db = tmp_path / "store.db"
    _create_file(db, embedder=CountingEmbedder()).close()
    e = CountingEmbedder()
    with _open_file(db, embedder=e) as g:
        a = g.add(group_key="note", content="A")
        b = g.add(group_key="note", content="B")
        c = g.add(group_key="note", content="C")
        e.embed_calls = 0
        e.embed_many_calls = 0
        g.update_many(
            [
                {"id": a.id, "content": "AA"},
                {"id": b.id, "group_key": "spell"},  # no content change → no embed
                {"id": c.id, "content": "CC"},
            ]
        )
        assert e.embed_many_calls == 1
        assert e.embed_calls == 0


def test_update_many_skips_embedder_entirely_when_no_content_changes(tmp_path):
    """Pin: a payload-only batch must not call the embedder at all."""
    db = tmp_path / "store.db"
    _create_file(db, embedder=CountingEmbedder()).close()
    e = CountingEmbedder()
    with _open_file(db, embedder=e) as g:
        a = g.add(group_key="note", content="A")
        b = g.add(group_key="note", content="B")
        e.embed_calls = 0
        e.embed_many_calls = 0
        g.update_many(
            [
                {"id": a.id, "payload": {"x": 1}},
                {"id": b.id, "group_key": "spell"},
            ]
        )
        assert e.embed_calls == 0
        assert e.embed_many_calls == 0


def test_update_many_atomic_on_embed_failure(tmp_path):
    """If embedding fails mid-batch, no records leak through partially."""

    class FailEmbedMany(FakeEmbedder):
        def embed_many(self, texts):
            raise RuntimeError("embed batch failed")

    db = tmp_path / "store.db"
    with _create_file(db, embedder=FakeEmbedder()) as g:
        a = g.add(group_key="note", content="original")

    with _open_file(db, embedder=FailEmbedMany()) as g:
        with pytest.raises(RuntimeError, match="embed batch failed"):
            g.update_many([{"id": a.id, "content": "new"}])
        # Original survived intact.
        fetched = g.get(a.id)
        assert fetched is not None
        assert fetched.content == "original"


def test_update_many_raises_on_duplicate_ids(tmp_path):
    with _create_file(tmp_path / "store.db", embedder=FakeEmbedder()) as g:
        a = g.add(group_key="note", content="hello")
        with pytest.raises(ValueError, match="duplicate"):
            g.update_many(
                [
                    {"id": a.id, "content": "v1"},
                    {"id": a.id, "content": "v2"},
                ]
            )


def test_update_many_preserves_omitted_fields(tmp_path):
    with _create_file(tmp_path / "store.db", embedder=FakeEmbedder()) as g:
        added = g.add(
            group_key="note",
            content="hi",
            payload={"k": "v"},
            threshold=0.5,
            keywords=["a"],
        )
        [updated] = g.update_many([{"id": added.id, "content": "bye"}])
        assert updated is not None
        assert updated.payload == {"k": "v"}
        assert updated.threshold == 0.5
        assert updated.keywords == ["a"]


def test_update_many_clears_nullable_fields_when_explicit_none(tmp_path):
    with _create_file(tmp_path / "store.db", embedder=FakeEmbedder()) as g:
        added = g.add(
            group_key="note",
            content="hi",
            payload={"k": "v"},
            threshold=0.5,
            keywords=["a"],
        )
        [updated] = g.update_many(
            [{"id": added.id, "payload": None, "threshold": None, "keywords": None}]
        )
        assert updated is not None
        assert updated.payload is None
        assert updated.threshold is None
        assert updated.keywords is None


def test_update_many_group_key_change_moves_partition_without_reembed(tmp_path):
    db = tmp_path / "store.db"
    _create_file(db, embedder=CountingEmbedder()).close()
    e = CountingEmbedder()
    with _open_file(db, embedder=e) as g:
        added = g.add(group_key="note", content="lumos")
        e.embed_calls = 0
        e.embed_many_calls = 0
        g.update_many([{"id": added.id, "group_key": "spell"}])
        assert e.embed_calls == 0
        assert e.embed_many_calls == 0
        assert g.vector_search("lumos", group_key="note", k=10) == []
        assert len(g.vector_search("lumos", group_key="spell", k=10)) == 1


def test_delete_many_returns_empty_list_on_empty_input(tmp_path):
    with _create_file(tmp_path / "store.db", embedder=FakeEmbedder()) as g:
        assert g.delete_many([]) == []


def test_delete_many_returns_bools_aligned_to_input(tmp_path):
    with _create_file(tmp_path / "store.db", embedder=FakeEmbedder()) as g:
        a = g.add(group_key="note", content="a")
        b = g.add(group_key="note", content="b")
        results = g.delete_many([a.id, "01HXXXXXXXXXXXXXXXXXXXXXXX", b.id])
        assert results == [True, False, True]
        assert g.get(a.id) is None
        assert g.get(b.id) is None


def test_delete_many_cascades_to_vectors_and_fts(tmp_path):
    with _create_file(tmp_path / "store.db", embedder=FakeEmbedder()) as g:
        a = g.add(group_key="note", content="ephemeral phoenix")
        b = g.add(group_key="note", content="another phoenix")
        g.delete_many([a.id, b.id])
        assert g.vector_search("phoenix", k=10) == []
        assert g.keyword_search("phoenix") == []


def test_delete_many_duplicate_ids_get_same_answer(tmp_path):
    with _create_file(tmp_path / "store.db", embedder=FakeEmbedder()) as g:
        a = g.add(group_key="note", content="hello")
        # Both occurrences should report True (existed at call time).
        results = g.delete_many([a.id, a.id])
        assert results == [True, True]
        assert g.get(a.id) is None


def test_delete_many_does_not_touch_unlisted_entries(tmp_path):
    with _create_file(tmp_path / "store.db", embedder=FakeEmbedder()) as g:
        keep = g.add(group_key="note", content="survivor")
        gone = g.add(group_key="note", content="doomed")
        g.delete_many([gone.id])
        assert g.get(keep.id) is not None
        assert g.get(gone.id) is None


# ---------- update ----------


def test_update_returns_none_for_missing_id(tmp_path):
    with _create_file(tmp_path / "store.db", embedder=FakeEmbedder()) as g:
        assert g.update("01HXXXXXXXXXXXXXXXXXXXXXXX", content="x") is None


def test_update_no_args_returns_unchanged_entry(tmp_path):
    with _create_file(tmp_path / "store.db", embedder=FakeEmbedder()) as g:
        added = g.add(group_key="note", content="hello", keywords=["a"])
        result = g.update(added.id)
        assert result is not None
        assert result.id == added.id
        assert result.content == "hello"
        assert result.group_key == "note"
        assert result.keywords == ["a"]


def test_update_changes_content_and_reindexes_search(tmp_path):
    with _create_file(tmp_path / "store.db", embedder=FakeEmbedder()) as g:
        added = g.add(group_key="note", content="the moon is full")
        updated = g.update(added.id, content="dragons fly at midnight")
        assert updated is not None
        assert updated.content == "dragons fly at midnight"

        # Vector search picks up new content; old query no longer hits.
        new_results = g.vector_search("dragons fly at midnight", k=1)
        assert new_results[0].id == added.id
        assert new_results[0].distance == 0.0

        # FTS reflects new tokens.
        assert g.keyword_search("moon") == []
        kw = g.keyword_search("dragons")
        assert len(kw) == 1
        assert kw[0].id == added.id


def test_update_changes_group_key_moves_vector_partition(tmp_path):
    with _create_file(tmp_path / "store.db", embedder=FakeEmbedder()) as g:
        added = g.add(group_key="note", content="lumos")
        g.update(added.id, group_key="spell")

        assert g.vector_search("lumos", group_key="note", k=10) == []
        spells = g.vector_search("lumos", group_key="spell", k=10)
        assert len(spells) == 1
        assert spells[0].id == added.id
        assert spells[0].group_key == "spell"


def test_update_group_key_only_does_not_reembed(tmp_path):
    """Pin: a group_key-only patch should not call the embedder."""
    db = tmp_path / "store.db"
    _create_file(db, embedder=CountingEmbedder()).close()
    e = CountingEmbedder()
    with _open_file(db, embedder=e) as g:
        added = g.add(group_key="note", content="hello")
        e.embed_calls = 0
        e.embed_many_calls = 0
        g.update(added.id, group_key="spell")
        assert e.embed_calls == 0
        assert e.embed_many_calls == 0


def test_update_content_only_calls_embed_once(tmp_path):
    db = tmp_path / "store.db"
    _create_file(db, embedder=CountingEmbedder()).close()
    e = CountingEmbedder()
    with _open_file(db, embedder=e) as g:
        added = g.add(group_key="note", content="hello")
        e.embed_calls = 0
        g.update(added.id, content="world")
        assert e.embed_calls == 1


def test_update_payload_only_does_not_reembed_or_reindex_fts(tmp_path):
    db = tmp_path / "store.db"
    _create_file(db, embedder=CountingEmbedder()).close()
    e = CountingEmbedder()
    with _open_file(db, embedder=e) as g:
        added = g.add(group_key="note", content="hello")
        e.embed_calls = 0
        result = g.update(added.id, payload={"foo": "bar"})
        assert e.embed_calls == 0
        assert result is not None
        assert result.payload == {"foo": "bar"}
        # FTS unaffected — content still findable.
        assert g.keyword_search("hello")[0].id == added.id


def test_update_clears_nullable_fields_when_passed_none(tmp_path):
    with _create_file(tmp_path / "store.db", embedder=FakeEmbedder()) as g:
        added = g.add(
            group_key="note",
            content="hello",
            payload={"k": "v"},
            threshold=0.5,
            keywords=["a"],
        )
        updated = g.update(added.id, payload=None, threshold=None, keywords=None)
        assert updated is not None
        assert updated.payload is None
        assert updated.threshold is None
        assert updated.keywords is None
        # Persists across re-fetch.
        fetched = g.get(added.id)
        assert fetched is not None
        assert fetched.payload is None
        assert fetched.threshold is None
        assert fetched.keywords is None


def test_update_omitted_nullable_fields_are_preserved(tmp_path):
    """Pin: omitting a nullable field must NOT clear it (the sentinel job)."""
    with _create_file(tmp_path / "store.db", embedder=FakeEmbedder()) as g:
        added = g.add(
            group_key="note",
            content="hello",
            payload={"k": "v"},
            threshold=0.5,
            keywords=["a"],
        )
        # Patch only content — every other field must survive.
        updated = g.update(added.id, content="world")
        assert updated is not None
        assert updated.payload == {"k": "v"}
        assert updated.threshold == 0.5
        assert updated.keywords == ["a"]


def test_update_keywords_reindexes_fts(tmp_path):
    with _create_file(tmp_path / "store.db", embedder=FakeEmbedder()) as g:
        added = g.add(group_key="note", content="A bird sings", keywords=["phoenix"])
        assert len(g.keyword_search("phoenix")) == 1
        g.update(added.id, keywords=["dragon"])
        assert g.keyword_search("phoenix") == []
        results = g.keyword_search("dragon")
        assert len(results) == 1
        assert results[0].id == added.id


def test_update_persists_across_reopens(tmp_path):
    db = tmp_path / "store.db"
    with _create_file(db, embedder=FakeEmbedder()) as g:
        added = g.add(group_key="note", content="hello")
        g.update(added.id, content="world", payload={"v": 1})

    with _open_file(db, embedder=FakeEmbedder()) as g:
        fetched = g.get(added.id)
        assert fetched is not None
        assert fetched.content == "world"
        assert fetched.payload == {"v": 1}


def test_update_preserves_id_and_created_at(tmp_path):
    """Updates must not reseat the entry's identity or its derived timestamp."""
    with _create_file(tmp_path / "store.db", embedder=FakeEmbedder()) as g:
        added = g.add(group_key="note", content="hello")
        before = added.created_at
        updated = g.update(added.id, content="world")
        assert updated is not None
        assert updated.id == added.id
        assert updated.created_at == before


def test_add_many_assigns_distinct_ids_in_input_order(tmp_path):
    with _create_file(tmp_path / "store.db", embedder=FakeEmbedder()) as g:
        added = g.add_many(
            [{"group_key": "note", "content": f"e{i}"} for i in range(10)]
        )
        ids = [e.id for e in added]
        assert len(set(ids)) == 10
        # ULIDs sort lexicographically by creation time, and add_many assigns
        # them in input order — so the input ordering matches the id ordering.
        assert ids == sorted(ids)


# --- group_ref + nullable group_key -----------------------------------------


def test_add_persists_group_ref(tmp_path):
    with _create_file(tmp_path / "store.db", embedder=FakeEmbedder()) as g:
        entry = g.add(group_key="doc", group_ref="path/to/file.md", content="hello")
        assert entry.group_ref == "path/to/file.md"
        fetched = g.get(entry.id)
        assert fetched is not None and fetched.group_ref == "path/to/file.md"


def test_group_ref_unique_within_group_key(tmp_path):
    with _create_file(tmp_path / "store.db", embedder=FakeEmbedder()) as g:
        g.add(group_key="doc", group_ref="r1", content="a")
        with pytest.raises(sqlite3.IntegrityError):
            g.add(group_key="doc", group_ref="r1", content="b")


def test_group_ref_same_value_allowed_across_group_keys(tmp_path):
    with _create_file(tmp_path / "store.db", embedder=FakeEmbedder()) as g:
        a = g.add(group_key="doc", group_ref="r1", content="a")
        b = g.add(group_key="page", group_ref="r1", content="b")
        assert a.id != b.id


def test_group_ref_unique_in_global_namespace(tmp_path):
    """group_key=None still enforces uniqueness on group_ref alone."""
    with _create_file(tmp_path / "store.db", embedder=FakeEmbedder()) as g:
        g.add(group_ref="g1", content="a")
        with pytest.raises(sqlite3.IntegrityError):
            g.add(group_ref="g1", content="b")


def test_group_ref_nulls_allowed_repeatedly(tmp_path):
    """SQLite treats NULLs as distinct; multiple entries without group_ref OK."""
    with _create_file(tmp_path / "store.db", embedder=FakeEmbedder()) as g:
        a = g.add(group_key="doc", content="a")
        b = g.add(group_key="doc", content="b")
        assert a.id != b.id
        assert a.group_ref is None and b.group_ref is None


def test_get_by_group_ref_within_group(tmp_path):
    with _create_file(tmp_path / "store.db", embedder=FakeEmbedder()) as g:
        added = g.add(group_key="doc", group_ref="r1", content="hello")
        g.add(group_key="page", group_ref="r1", content="other")
        found = g.get_by_group_ref(group_key="doc", group_ref="r1")
        assert found is not None and found.id == added.id


def test_get_by_group_ref_in_global_namespace(tmp_path):
    with _create_file(tmp_path / "store.db", embedder=FakeEmbedder()) as g:
        added = g.add(group_ref="g1", content="hello")
        g.add(group_key="doc", group_ref="g1", content="other")
        found = g.get_by_group_ref(group_key=None, group_ref="g1")
        assert found is not None and found.id == added.id


def test_get_by_group_ref_returns_none_for_unknown(tmp_path):
    with _create_file(tmp_path / "store.db", embedder=FakeEmbedder()) as g:
        assert g.get_by_group_ref(group_key="doc", group_ref="missing") is None


def test_nullable_group_key_in_add_and_search(tmp_path):
    with _create_file(tmp_path / "store.db", embedder=FakeEmbedder()) as g:
        ungrouped = g.add(content="lumos")
        grouped = g.add(group_key="spell", content="lumos")
        assert ungrouped.group_key is None
        # No-filter search returns both.
        all_results = g.vector_search("lumos", k=10)
        ids = {r.id for r in all_results}
        assert ungrouped.id in ids and grouped.id in ids
        # group_key="spell" filter excludes the ungrouped one.
        spell_results = g.vector_search("lumos", group_key="spell", k=10)
        assert {r.id for r in spell_results} == {grouped.id}


def test_update_clears_group_key_to_none(tmp_path):
    with _create_file(tmp_path / "store.db", embedder=FakeEmbedder()) as g:
        added = g.add(group_key="doc", content="hello")
        updated = g.update(added.id, group_key=None)
        assert updated is not None and updated.group_key is None
        # The vector partition moved — verify the entry is still findable.
        results = g.vector_search("hello", k=5)
        assert any(r.id == added.id for r in results)


def test_update_sets_and_clears_group_ref(tmp_path):
    with _create_file(tmp_path / "store.db", embedder=FakeEmbedder()) as g:
        added = g.add(group_key="doc", content="hello")
        updated = g.update(added.id, group_ref="r1")
        assert updated is not None and updated.group_ref == "r1"
        cleared = g.update(added.id, group_ref=None)
        assert cleared is not None and cleared.group_ref is None


def test_update_to_colliding_group_ref_raises(tmp_path):
    with _create_file(tmp_path / "store.db", embedder=FakeEmbedder()) as g:
        g.add(group_key="doc", group_ref="r1", content="a")
        b = g.add(group_key="doc", content="b")
        with pytest.raises(sqlite3.IntegrityError):
            g.update(b.id, group_ref="r1")


def test_list_filters_by_group_ref(tmp_path):
    with _create_file(tmp_path / "store.db", embedder=FakeEmbedder()) as g:
        a = g.add(group_key="doc", group_ref="r1", content="a")
        g.add(group_key="doc", group_ref="r2", content="b")
        b = g.add(group_key="page", group_ref="r1", content="c")
        results = g.list(group_ref="r1")
        assert {r.id for r in results} == {a.id, b.id}


def test_list_filters_by_group_key_and_group_ref(tmp_path):
    with _create_file(tmp_path / "store.db", embedder=FakeEmbedder()) as g:
        a = g.add(group_key="doc", group_ref="r1", content="a")
        g.add(group_key="doc", group_ref="r2", content="b")
        g.add(group_key="page", group_ref="r1", content="c")
        results = g.list(group_key="doc", group_ref="r1")
        assert [r.id for r in results] == [a.id]

import hashlib
import sqlite3
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


class CountingEmbedder(FakeEmbedder):
    """A FakeEmbedder that records how many times `embed` is called."""

    def __init__(self, *, model: str = "fake-v1", dimension: int = 8) -> None:
        super().__init__(model=model, dimension=dimension)
        self.embed_calls = 0

    def embed(self, text: str) -> list[float]:
        self.embed_calls += 1
        return super().embed(text)


def test_init_creates_file_idempotently(tmp_path):
    db = tmp_path / "store.db"
    Grimoire.init(db, embedder=FakeEmbedder()).close()
    Grimoire.init(db, embedder=FakeEmbedder()).close()
    assert db.exists()


def test_embedder_model_mismatch_raises(tmp_path):
    db = tmp_path / "store.db"
    Grimoire.init(db, embedder=FakeEmbedder(model="alpha")).close()
    with pytest.raises(GrimoireMismatch):
        Grimoire.open(db, embedder=FakeEmbedder(model="beta"))


def test_embedder_dimension_mismatch_raises(tmp_path):
    db = tmp_path / "store.db"
    Grimoire.init(db, embedder=FakeEmbedder(dimension=8)).close()
    with pytest.raises(GrimoireMismatch):
        Grimoire.open(db, embedder=FakeEmbedder(dimension=16))


def test_add_returns_entry(tmp_path):
    with Grimoire.init(tmp_path / "store.db", embedder=FakeEmbedder()) as g:
        entry = g.add(kind="note", content="the moon is full")
        assert isinstance(entry, Entry)
        assert entry.kind == "note"
        assert entry.content == "the moon is full"


def test_search_finds_exact_match_first(tmp_path):
    with Grimoire.init(tmp_path / "store.db", embedder=FakeEmbedder()) as g:
        g.add(kind="note", content="the moon is full")
        g.add(kind="note", content="dragons fly at midnight")
        g.add(kind="note", content="potions bubble in the cauldron")

        results = g.vector_search("the moon is full", k=3)
        assert len(results) == 3
        assert results[0].content == "the moon is full"
        assert results[0].distance == 0.0


def test_search_filters_by_kind(tmp_path):
    with Grimoire.init(tmp_path / "store.db", embedder=FakeEmbedder()) as g:
        g.add(kind="spell", content="lumos")
        g.add(kind="potion", content="lumos")

        results = g.vector_search("lumos", kind="spell", k=10)
        assert len(results) == 1
        assert results[0].kind == "spell"


def test_dynamic_threshold_drops_low_match(tmp_path):
    with Grimoire.init(tmp_path / "store.db", embedder=FakeEmbedder()) as g:
        g.add(kind="note", content="the moon is full", threshold=0.0)
        g.add(kind="note", content="dragons fly at midnight", threshold=0.0)

        all_results = g.vector_search("the moon is full", k=10)
        assert len(all_results) == 2

        gated = g.vector_search("the moon is full", k=10, dynamic_threshold=True)
        assert len(gated) == 1
        assert gated[0].content == "the moon is full"


def test_two_files_are_independent(tmp_path):
    a_path = tmp_path / "a.db"
    b_path = tmp_path / "b.db"
    with Grimoire.init(a_path, embedder=FakeEmbedder()) as a:
        a.add(kind="note", content="alpha")
    with Grimoire.init(b_path, embedder=FakeEmbedder()) as b:
        b.add(kind="note", content="beta")
        results = b.vector_search("alpha", k=10)
        assert all(r.content != "alpha" for r in results)


def test_data_persists_across_reopens(tmp_path):
    db = tmp_path / "store.db"
    with Grimoire.init(db, embedder=FakeEmbedder()) as g:
        g.add(kind="note", content="the moon is full")

    with Grimoire.open(db, embedder=FakeEmbedder()) as g:
        results = g.vector_search("the moon is full", k=1)
        assert len(results) == 1
        assert results[0].content == "the moon is full"


def test_get_returns_entry(tmp_path):
    with Grimoire.init(tmp_path / "store.db", embedder=FakeEmbedder()) as g:
        added = g.add(kind="note", content="lumos")
        fetched = g.get(added.id)
        assert fetched is not None
        assert fetched.id == added.id
        assert fetched.content == "lumos"


def test_get_returns_none_for_missing_id(tmp_path):
    with Grimoire.init(tmp_path / "store.db", embedder=FakeEmbedder()) as g:
        assert g.get("01HXXXXXXXXXXXXXXXXXXXXXXX") is None


def test_list_returns_all_entries_in_chronological_order(tmp_path):
    with Grimoire.init(tmp_path / "store.db", embedder=FakeEmbedder()) as g:
        a = g.add(kind="note", content="first")
        b = g.add(kind="note", content="second")
        c = g.add(kind="note", content="third")
        results = g.list()
        assert [r.id for r in results] == [a.id, b.id, c.id]


def test_list_filters_by_kind(tmp_path):
    with Grimoire.init(tmp_path / "store.db", embedder=FakeEmbedder()) as g:
        g.add(kind="spell", content="lumos")
        g.add(kind="potion", content="felix felicis")
        g.add(kind="spell", content="alohomora")

        spells = g.list(kind="spell")
        assert len(spells) == 2
        assert all(r.kind == "spell" for r in spells)


def test_list_paginates_via_after_id(tmp_path):
    with Grimoire.init(tmp_path / "store.db", embedder=FakeEmbedder()) as g:
        added = [g.add(kind="note", content=f"e{i}") for i in range(5)]

        page1 = g.list(limit=2)
        assert [r.id for r in page1] == [added[0].id, added[1].id]

        page2 = g.list(limit=2, after_id=page1[-1].id)
        assert [r.id for r in page2] == [added[2].id, added[3].id]

        page3 = g.list(limit=2, after_id=page2[-1].id)
        assert [r.id for r in page3] == [added[4].id]

        page4 = g.list(limit=2, after_id=page3[-1].id)
        assert page4 == []


def test_list_respects_limit(tmp_path):
    with Grimoire.init(tmp_path / "store.db", embedder=FakeEmbedder()) as g:
        for i in range(5):
            g.add(kind="note", content=f"e{i}")
        assert len(g.list(limit=3)) == 3


def test_delete_removes_entry_and_vector(tmp_path):
    with Grimoire.init(tmp_path / "store.db", embedder=FakeEmbedder()) as g:
        added = g.add(kind="note", content="ephemeral")
        assert g.delete(added.id) is True
        assert g.get(added.id) is None

        # Search should no longer return it.
        results = g.vector_search("ephemeral", k=10)
        assert all(r.id != added.id for r in results)


def test_delete_returns_false_for_missing_id(tmp_path):
    with Grimoire.init(tmp_path / "store.db", embedder=FakeEmbedder()) as g:
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
        Grimoire.init(db, embedder=_BadDimensionEmbedder())
    assert not db.exists() or db.stat().st_size == 0


def test_embedder_with_zero_dimension_rejected(tmp_path):
    with pytest.raises(InvalidEmbedder):
        Grimoire.init(tmp_path / "store.db", embedder=_NonPositiveDimensionEmbedder())


def test_embedder_with_empty_model_rejected(tmp_path):
    with pytest.raises(InvalidEmbedder):
        Grimoire.init(tmp_path / "store.db", embedder=_EmptyModelEmbedder())


# ---------- init ----------


def test_init_exercises_embedder_exactly_once_on_create(tmp_path):
    e = CountingEmbedder()
    Grimoire.init(tmp_path / "store.db", embedder=e).close()
    assert e.embed_calls == 1


def test_init_exercises_embedder_again_on_reinit(tmp_path):
    db = tmp_path / "store.db"
    e = CountingEmbedder()
    Grimoire.init(db, embedder=e).close()
    Grimoire.init(db, embedder=e).close()
    assert e.embed_calls == 2


def test_init_creates_parent_directories(tmp_path):
    nested = tmp_path / "deep" / "nested" / "store.db"
    Grimoire.init(nested, embedder=FakeEmbedder()).close()
    assert nested.exists()


def test_init_propagates_embedder_errors(tmp_path):
    class BoomEmbedder(FakeEmbedder):
        def embed(self, text: str) -> list[float]:
            raise RuntimeError("model fetch failed")

    with pytest.raises(RuntimeError, match="model fetch failed"):
        Grimoire.init(tmp_path / "store.db", embedder=BoomEmbedder())


def test_init_raises_mismatch_on_lock_conflict(tmp_path):
    db = tmp_path / "store.db"
    Grimoire.init(db, embedder=FakeEmbedder(model="alpha")).close()
    with pytest.raises(GrimoireMismatch):
        Grimoire.init(db, embedder=FakeEmbedder(model="beta"))


# ---------- open ----------


def test_open_raises_not_found_for_missing_path(tmp_path):
    with pytest.raises(GrimoireNotFound):
        Grimoire.open(tmp_path / "nope.db", embedder=FakeEmbedder())


def test_open_raises_not_found_for_non_grimoire_file(tmp_path):
    db = tmp_path / "stranger.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE other (x INTEGER)")
    conn.commit()
    conn.close()
    with pytest.raises(GrimoireNotFound):
        Grimoire.open(db, embedder=FakeEmbedder())


def test_open_does_not_call_embed(tmp_path):
    """Regression pin: open() must stay cheap — no embedder.embed() during setup."""
    db = tmp_path / "store.db"
    Grimoire.init(db, embedder=FakeEmbedder()).close()
    e = CountingEmbedder()
    Grimoire.open(db, embedder=e).close()
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
    with Grimoire.init(db, embedder=FakeEmbedder(model="m1", dimension=8)) as g:
        g.add(kind="note", content="alpha")
        g.add(kind="note", content="beta")
        g.add(kind="spell", content="lumos")

    stats = Grimoire.peek(db)
    assert isinstance(stats, Stats)
    assert stats.model == "m1"
    assert stats.dimension == 8
    assert stats.schema_version == 2
    assert stats.entry_count == 3
    assert stats.kinds == {"note": 2, "spell": 1}


# ---------- age-windowed reads ----------


def test_entry_created_at_round_trips_to_ulid_timestamp():
    moment = datetime(2026, 5, 4, 10, 30, 0, tzinfo=UTC)
    entry_id = str(ULID.from_datetime(moment))
    entry = Entry(id=entry_id, kind="note", content="x")
    assert entry.created_at == moment


def test_list_created_after_is_inclusive_lower_bound(tmp_path):
    with Grimoire.init(tmp_path / "store.db", embedder=FakeEmbedder()) as g:
        a = g.add(kind="note", content="a")
        time.sleep(0.005)
        b = g.add(kind="note", content="b")
        time.sleep(0.005)
        c = g.add(kind="note", content="c")

        results = g.list(created_after=b.created_at)
        assert [r.id for r in results] == [b.id, c.id]
        assert a.id not in [r.id for r in results]


def test_list_created_before_is_exclusive_upper_bound(tmp_path):
    with Grimoire.init(tmp_path / "store.db", embedder=FakeEmbedder()) as g:
        a = g.add(kind="note", content="a")
        time.sleep(0.005)
        b = g.add(kind="note", content="b")
        time.sleep(0.005)
        c = g.add(kind="note", content="c")

        results = g.list(created_before=c.created_at)
        assert [r.id for r in results] == [a.id, b.id]


def test_list_combines_both_bounds(tmp_path):
    with Grimoire.init(tmp_path / "store.db", embedder=FakeEmbedder()) as g:
        g.add(kind="note", content="a")
        time.sleep(0.005)
        b = g.add(kind="note", content="b")
        time.sleep(0.005)
        c = g.add(kind="note", content="c")
        time.sleep(0.005)
        g.add(kind="note", content="d")

        results = g.list(created_after=b.created_at, created_before=c.created_at)
        assert [r.id for r in results] == [b.id]


def test_search_honors_created_after(tmp_path):
    with Grimoire.init(tmp_path / "store.db", embedder=FakeEmbedder()) as g:
        old = g.add(kind="note", content="lumos")
        time.sleep(0.005)
        new = g.add(kind="note", content="lumos")

        results = g.vector_search("lumos", k=10, created_after=new.created_at)
        ids = [r.id for r in results]
        assert new.id in ids
        assert old.id not in ids


def test_search_honors_created_before(tmp_path):
    with Grimoire.init(tmp_path / "store.db", embedder=FakeEmbedder()) as g:
        old = g.add(kind="note", content="lumos")
        time.sleep(0.005)
        new = g.add(kind="note", content="lumos")

        results = g.vector_search("lumos", k=10, created_before=new.created_at)
        ids = [r.id for r in results]
        assert old.id in ids
        assert new.id not in ids


def test_list_window_excludes_everything_before_grimoire(tmp_path):
    with Grimoire.init(tmp_path / "store.db", embedder=FakeEmbedder()) as g:
        g.add(kind="note", content="a")
        g.add(kind="note", content="b")

        far_future = datetime.now(tz=UTC) + timedelta(days=365)
        assert g.list(created_after=far_future) == []


# ---------- keyword search ----------


def test_keyword_search_finds_exact_token(tmp_path):
    with Grimoire.init(tmp_path / "store.db", embedder=FakeEmbedder()) as g:
        g.add(kind="note", content="the moon is full")
        g.add(kind="note", content="dragons fly at midnight")
        g.add(kind="note", content="potions bubble in the cauldron")

        results = g.keyword_search("moon")
        assert len(results) == 1
        assert results[0].content == "the moon is full"


def test_keyword_search_populates_rank(tmp_path):
    with Grimoire.init(tmp_path / "store.db", embedder=FakeEmbedder()) as g:
        g.add(kind="note", content="the moon is full")
        results = g.keyword_search("moon")
        assert results[0].rank is not None


def test_keyword_search_filters_by_kind(tmp_path):
    with Grimoire.init(tmp_path / "store.db", embedder=FakeEmbedder()) as g:
        g.add(kind="spell", content="lumos lights the way")
        g.add(kind="potion", content="lumos is also a potion")

        results = g.keyword_search("lumos", kind="spell")
        assert len(results) == 1
        assert results[0].kind == "spell"


def test_keyword_search_returns_empty_on_no_match(tmp_path):
    with Grimoire.init(tmp_path / "store.db", embedder=FakeEmbedder()) as g:
        g.add(kind="note", content="dragons fly at midnight")
        assert g.keyword_search("phoenix") == []


def test_keyword_search_respects_k(tmp_path):
    with Grimoire.init(tmp_path / "store.db", embedder=FakeEmbedder()) as g:
        for i in range(5):
            g.add(kind="note", content=f"dragon {i}")
        assert len(g.keyword_search("dragon", k=2)) == 2


def test_keyword_search_honors_created_after(tmp_path):
    with Grimoire.init(tmp_path / "store.db", embedder=FakeEmbedder()) as g:
        old = g.add(kind="note", content="dragon old")
        time.sleep(0.005)
        new = g.add(kind="note", content="dragon new")
        results = g.keyword_search("dragon", created_after=new.created_at)
        ids = [r.id for r in results]
        assert new.id in ids
        assert old.id not in ids


def test_keyword_search_honors_created_before(tmp_path):
    with Grimoire.init(tmp_path / "store.db", embedder=FakeEmbedder()) as g:
        old = g.add(kind="note", content="dragon old")
        time.sleep(0.005)
        new = g.add(kind="note", content="dragon new")
        results = g.keyword_search("dragon", created_before=new.created_at)
        ids = [r.id for r in results]
        assert old.id in ids
        assert new.id not in ids


def test_delete_removes_keyword_index_too(tmp_path):
    with Grimoire.init(tmp_path / "store.db", embedder=FakeEmbedder()) as g:
        added = g.add(kind="note", content="ephemeral phoenix")
        assert g.delete(added.id) is True
        assert g.keyword_search("phoenix") == []


def test_peek_does_not_require_embedder_or_extension(tmp_path):
    # peek must be safe on a freshly-created file from another process,
    # without sqlite-vec or an embedder loaded.
    db = tmp_path / "store.db"
    Grimoire.init(db, embedder=FakeEmbedder()).close()
    stats = Grimoire.peek(db)
    assert stats is not None
    assert stats.entry_count == 0
    assert stats.kinds == {}

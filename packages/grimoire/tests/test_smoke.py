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
    Mount,
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
    Grimoire(mount=tmp_path, embedder=FakeEmbedder()).close()
    Grimoire(mount=tmp_path, embedder=FakeEmbedder()).close()
    assert (tmp_path / "grimoire.db").exists()


def test_init_enables_wal_journal_mode(tmp_path):
    Grimoire(mount=tmp_path, embedder=FakeEmbedder()).close()
    # Verify via a raw connection — the journal mode persists in the db
    # header, so any subsequent open inherits it.
    raw = sqlite3.connect(tmp_path / "grimoire.db")
    try:
        mode = raw.execute("PRAGMA journal_mode").fetchone()[0]
    finally:
        raw.close()
    assert mode.lower() == "wal"


def test_embedder_model_mismatch_raises(tmp_path):
    Grimoire(mount=tmp_path, embedder=FakeEmbedder(model="alpha")).close()
    with pytest.raises(GrimoireMismatch):
        Grimoire(mount=tmp_path, embedder=FakeEmbedder(model="beta"))


def test_embedder_dimension_mismatch_raises(tmp_path):
    Grimoire(mount=tmp_path, embedder=FakeEmbedder(dimension=8)).close()
    with pytest.raises(GrimoireMismatch):
        Grimoire(mount=tmp_path, embedder=FakeEmbedder(dimension=16))


def test_add_returns_entry(tmp_path):
    with Grimoire(mount=tmp_path, embedder=FakeEmbedder()) as g:
        entry = g.add(group_key="note", vector_text="the moon is full")
        assert isinstance(entry, Entry)
        assert entry.group_key == "note"
        assert entry.vector_text == "the moon is full"


def test_search_finds_exact_match_first(tmp_path):
    with Grimoire(mount=tmp_path, embedder=FakeEmbedder()) as g:
        g.add(group_key="note", vector_text="the moon is full")
        g.add(group_key="note", vector_text="dragons fly at midnight")
        g.add(group_key="note", vector_text="potions bubble in the cauldron")

        results = g.vector_search("the moon is full", k=3)
        assert len(results) == 3
        assert results[0].vector_text == "the moon is full"
        assert results[0].distance == 0.0


def test_search_filters_by_group_key(tmp_path):
    with Grimoire(mount=tmp_path, embedder=FakeEmbedder()) as g:
        g.add(group_key="spell", vector_text="lumos")
        g.add(group_key="potion", vector_text="lumos")

        results = g.vector_search("lumos", group_key="spell", k=10)
        assert len(results) == 1
        assert results[0].group_key == "spell"


def test_dynamic_threshold_drops_low_match(tmp_path):
    with Grimoire(mount=tmp_path, embedder=FakeEmbedder()) as g:
        g.add(group_key="note", vector_text="the moon is full", threshold=0.0)
        g.add(group_key="note", vector_text="dragons fly at midnight", threshold=0.0)

        all_results = g.vector_search("the moon is full", k=10)
        assert len(all_results) == 2

        gated = g.vector_search("the moon is full", k=10, dynamic_threshold=True)
        assert len(gated) == 1
        assert gated[0].vector_text == "the moon is full"


def test_two_dbs_are_independent(tmp_path):
    with Grimoire("a", mount=tmp_path, embedder=FakeEmbedder()) as a:
        a.add(group_key="note", vector_text="alpha")
    with Grimoire("b", mount=tmp_path, embedder=FakeEmbedder()) as b:
        b.add(group_key="note", vector_text="beta")
        results = b.vector_search("alpha", k=10)
        assert all(r.vector_text != "alpha" for r in results)


def test_data_persists_across_reopens(tmp_path):
    with Grimoire(mount=tmp_path, embedder=FakeEmbedder()) as g:
        g.add(group_key="note", vector_text="the moon is full")

    with Grimoire(mount=tmp_path, embedder=FakeEmbedder()) as g:
        results = g.vector_search("the moon is full", k=1)
        assert len(results) == 1
        assert results[0].vector_text == "the moon is full"


def test_get_returns_entry(tmp_path):
    with Grimoire(mount=tmp_path, embedder=FakeEmbedder()) as g:
        added = g.add(group_key="note", vector_text="lumos")
        fetched = g.get(added.id)
        assert fetched is not None
        assert fetched.id == added.id
        assert fetched.vector_text == "lumos"


def test_get_returns_none_for_missing_id(tmp_path):
    with Grimoire(mount=tmp_path, embedder=FakeEmbedder()) as g:
        assert g.get("01HXXXXXXXXXXXXXXXXXXXXXXX") is None


def test_list_returns_all_entries_in_chronological_order(tmp_path):
    with Grimoire(mount=tmp_path, embedder=FakeEmbedder()) as g:
        a = g.add(group_key="note", vector_text="first")
        b = g.add(group_key="note", vector_text="second")
        c = g.add(group_key="note", vector_text="third")
        results = g.list()
        assert [r.id for r in results] == [a.id, b.id, c.id]


def test_list_filters_by_group_key(tmp_path):
    with Grimoire(mount=tmp_path, embedder=FakeEmbedder()) as g:
        g.add(group_key="spell", vector_text="lumos")
        g.add(group_key="potion", vector_text="felix felicis")
        g.add(group_key="spell", vector_text="alohomora")

        spells = g.list(group_key="spell")
        assert len(spells) == 2
        assert all(r.group_key == "spell" for r in spells)


def test_list_paginates_via_after_id(tmp_path):
    with Grimoire(mount=tmp_path, embedder=FakeEmbedder()) as g:
        added = [g.add(group_key="note", vector_text=f"e{i}") for i in range(5)]

        page1 = g.list(limit=2)
        assert [r.id for r in page1] == [added[0].id, added[1].id]

        page2 = g.list(limit=2, after_id=page1[-1].id)
        assert [r.id for r in page2] == [added[2].id, added[3].id]

        page3 = g.list(limit=2, after_id=page2[-1].id)
        assert [r.id for r in page3] == [added[4].id]

        page4 = g.list(limit=2, after_id=page3[-1].id)
        assert page4 == []


def test_list_respects_limit(tmp_path):
    with Grimoire(mount=tmp_path, embedder=FakeEmbedder()) as g:
        for i in range(5):
            g.add(group_key="note", vector_text=f"e{i}")
        assert len(g.list(limit=3)) == 3


def test_delete_removes_entry_and_vector(tmp_path):
    with Grimoire(mount=tmp_path, embedder=FakeEmbedder()) as g:
        added = g.add(group_key="note", vector_text="ephemeral")
        assert g.delete(added.id) is True
        assert g.get(added.id) is None

        # Search should no longer return it.
        results = g.vector_search("ephemeral", k=10)
        assert all(r.id != added.id for r in results)


def test_delete_returns_false_for_missing_id(tmp_path):
    with Grimoire(mount=tmp_path, embedder=FakeEmbedder()) as g:
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
    with pytest.raises(InvalidEmbedder):
        Grimoire(mount=tmp_path, embedder=_BadDimensionEmbedder())
    db = tmp_path / "grimoire.db"
    assert not db.exists() or db.stat().st_size == 0


def test_embedder_with_zero_dimension_rejected(tmp_path):
    with pytest.raises(InvalidEmbedder):
        Grimoire(mount=tmp_path, embedder=_NonPositiveDimensionEmbedder())


def test_embedder_with_empty_model_rejected(tmp_path):
    with pytest.raises(InvalidEmbedder):
        Grimoire(mount=tmp_path, embedder=_EmptyModelEmbedder())


# ---------- init / create branch ----------


def test_init_exercises_embedder_exactly_once_on_create(tmp_path):
    e = CountingEmbedder()
    Grimoire(mount=tmp_path, embedder=e).close()
    assert e.embed_calls == 1


def test_init_does_not_reembed_on_reopen(tmp_path):
    """Reopening an existing DB attaches without warming the embedder."""
    Grimoire(mount=tmp_path, embedder=FakeEmbedder()).close()
    e = CountingEmbedder()
    Grimoire(mount=tmp_path, embedder=e).close()
    assert e.embed_calls == 0


def test_init_creates_parent_directories(tmp_path):
    nested = tmp_path / "deep" / "nested"
    Grimoire(mount=nested, embedder=FakeEmbedder()).close()
    assert (nested / "grimoire.db").exists()


def test_init_propagates_embedder_errors(tmp_path):
    class BoomEmbedder(FakeEmbedder):
        def embed(self, text: str) -> list[float]:
            raise RuntimeError("model fetch failed")

    with pytest.raises(RuntimeError, match="model fetch failed"):
        Grimoire(mount=tmp_path, embedder=BoomEmbedder())


def test_init_raises_mismatch_on_lock_conflict(tmp_path):
    Grimoire(mount=tmp_path, embedder=FakeEmbedder(model="alpha")).close()
    with pytest.raises(GrimoireMismatch):
        Grimoire(mount=tmp_path, embedder=FakeEmbedder(model="beta"))


# ---------- attach branch ----------


def test_attach_raises_not_found_for_missing_path(tmp_path):
    """No DB and no embedder consent — raise rather than create silently."""
    with pytest.raises(GrimoireNotFound):
        Grimoire(mount=tmp_path)


def test_attach_raises_not_found_for_non_grimoire_file(tmp_path):
    """A stranger SQLite file at the default DB path is not a grimoire."""
    db = tmp_path / "grimoire.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE other (x INTEGER)")
    conn.commit()
    conn.close()
    with pytest.raises(GrimoireNotFound):
        Grimoire(mount=tmp_path, embedder=FakeEmbedder())


def test_attach_does_not_call_embed(tmp_path):
    """Regression pin: attach must stay cheap — no embedder.embed() during setup."""
    Grimoire(mount=tmp_path, embedder=FakeEmbedder()).close()
    e = CountingEmbedder()
    Grimoire(mount=tmp_path, embedder=e).close()
    assert e.embed_calls == 0


# ---------- peek (via Mount) ----------


def test_peek_returns_none_for_missing_file(tmp_path):
    # Mount must exist for `Mount(...).peek()` to be callable.
    Mount(tmp_path, create=True)
    assert Mount(tmp_path).peek() is None


def test_peek_returns_none_for_non_grimoire_file(tmp_path):
    db = tmp_path / "grimoire.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE other (x INTEGER)")
    conn.commit()
    conn.close()
    assert Mount(tmp_path).peek() is None


def test_peek_returns_stats_for_initialized_grimoire(tmp_path):
    with Grimoire(mount=tmp_path, embedder=FakeEmbedder(model="m1", dimension=8)) as g:
        g.add(group_key="note", vector_text="alpha")
        g.add(group_key="note", vector_text="beta")
        g.add(group_key="spell", vector_text="lumos")

    stats = Mount(tmp_path).peek()
    assert isinstance(stats, Stats)
    assert stats.model == "m1"
    assert stats.dimension == 8
    assert stats.schema_version == 2
    assert stats.entry_count == 3
    assert stats.groups == {"note": 2, "spell": 1}


def test_peek_does_not_require_embedder_or_extension(tmp_path):
    """peek must be safe on a freshly-created file from another process."""
    Grimoire(mount=tmp_path, embedder=FakeEmbedder()).close()
    stats = Mount(tmp_path).peek()
    assert stats is not None
    assert stats.entry_count == 0
    assert stats.groups == {}


# ---------- age-windowed reads ----------


def test_entry_created_at_round_trips_to_ulid_timestamp():
    moment = datetime(2026, 5, 4, 10, 30, 0, tzinfo=UTC)
    entry_id = str(ULID.from_datetime(moment))
    entry = Entry(id=entry_id, group_key="note", vector_text="x")
    assert entry.created_at == moment


def test_list_created_after_is_inclusive_lower_bound(tmp_path):
    with Grimoire(mount=tmp_path, embedder=FakeEmbedder()) as g:
        a = g.add(group_key="note", vector_text="a")
        time.sleep(0.005)
        b = g.add(group_key="note", vector_text="b")
        time.sleep(0.005)
        c = g.add(group_key="note", vector_text="c")

        results = g.list(created_after=b.created_at)
        assert [r.id for r in results] == [b.id, c.id]
        assert a.id not in [r.id for r in results]


def test_list_created_before_is_exclusive_upper_bound(tmp_path):
    with Grimoire(mount=tmp_path, embedder=FakeEmbedder()) as g:
        a = g.add(group_key="note", vector_text="a")
        time.sleep(0.005)
        b = g.add(group_key="note", vector_text="b")
        time.sleep(0.005)
        c = g.add(group_key="note", vector_text="c")

        results = g.list(created_before=c.created_at)
        assert [r.id for r in results] == [a.id, b.id]


def test_list_combines_both_bounds(tmp_path):
    with Grimoire(mount=tmp_path, embedder=FakeEmbedder()) as g:
        g.add(group_key="note", vector_text="a")
        time.sleep(0.005)
        b = g.add(group_key="note", vector_text="b")
        time.sleep(0.005)
        c = g.add(group_key="note", vector_text="c")
        time.sleep(0.005)
        g.add(group_key="note", vector_text="d")

        results = g.list(created_after=b.created_at, created_before=c.created_at)
        assert [r.id for r in results] == [b.id]


def test_search_honors_created_after(tmp_path):
    with Grimoire(mount=tmp_path, embedder=FakeEmbedder()) as g:
        old = g.add(group_key="note", vector_text="lumos")
        time.sleep(0.005)
        new = g.add(group_key="note", vector_text="lumos")

        results = g.vector_search("lumos", k=10, created_after=new.created_at)
        ids = [r.id for r in results]
        assert new.id in ids
        assert old.id not in ids


def test_search_honors_created_before(tmp_path):
    with Grimoire(mount=tmp_path, embedder=FakeEmbedder()) as g:
        old = g.add(group_key="note", vector_text="lumos")
        time.sleep(0.005)
        new = g.add(group_key="note", vector_text="lumos")

        results = g.vector_search("lumos", k=10, created_before=new.created_at)
        ids = [r.id for r in results]
        assert old.id in ids
        assert new.id not in ids


def test_list_window_excludes_everything_before_grimoire(tmp_path):
    with Grimoire(mount=tmp_path, embedder=FakeEmbedder()) as g:
        g.add(group_key="note", vector_text="a")
        g.add(group_key="note", vector_text="b")

        far_future = datetime.now(tz=UTC) + timedelta(days=365)
        assert g.list(created_after=far_future) == []


# ---------- keyword search ----------


def test_keyword_search_finds_exact_token(tmp_path):
    with Grimoire(mount=tmp_path, embedder=FakeEmbedder()) as g:
        g.add(group_key="note", keyword_text="the moon is full")
        g.add(group_key="note", keyword_text="dragons fly at midnight")
        g.add(group_key="note", keyword_text="potions bubble in the cauldron")

        results = g.keyword_search("moon")
        assert len(results) == 1
        assert results[0].keyword_text == "the moon is full"


def test_keyword_search_populates_rank(tmp_path):
    with Grimoire(mount=tmp_path, embedder=FakeEmbedder()) as g:
        g.add(group_key="note", keyword_text="the moon is full")
        results = g.keyword_search("moon")
        assert results[0].rank is not None


def test_keyword_search_filters_by_group_key(tmp_path):
    with Grimoire(mount=tmp_path, embedder=FakeEmbedder()) as g:
        g.add(group_key="spell", keyword_text="lumos lights the way")
        g.add(group_key="potion", keyword_text="lumos is also a potion")

        results = g.keyword_search("lumos", group_key="spell")
        assert len(results) == 1
        assert results[0].group_key == "spell"


def test_keyword_search_returns_empty_on_no_match(tmp_path):
    with Grimoire(mount=tmp_path, embedder=FakeEmbedder()) as g:
        g.add(group_key="note", keyword_text="dragons fly at midnight")
        assert g.keyword_search("phoenix") == []


def test_keyword_search_respects_k(tmp_path):
    with Grimoire(mount=tmp_path, embedder=FakeEmbedder()) as g:
        for i in range(5):
            g.add(group_key="note", keyword_text=f"dragon {i}")
        assert len(g.keyword_search("dragon", k=2)) == 2


def test_keyword_search_honors_created_after(tmp_path):
    with Grimoire(mount=tmp_path, embedder=FakeEmbedder()) as g:
        old = g.add(group_key="note", keyword_text="dragon old")
        time.sleep(0.005)
        new = g.add(group_key="note", keyword_text="dragon new")
        results = g.keyword_search("dragon", created_after=new.created_at)
        ids = [r.id for r in results]
        assert new.id in ids
        assert old.id not in ids


def test_keyword_search_honors_created_before(tmp_path):
    with Grimoire(mount=tmp_path, embedder=FakeEmbedder()) as g:
        old = g.add(group_key="note", keyword_text="dragon old")
        time.sleep(0.005)
        new = g.add(group_key="note", keyword_text="dragon new")
        results = g.keyword_search("dragon", created_before=new.created_at)
        ids = [r.id for r in results]
        assert old.id in ids
        assert new.id not in ids


def test_delete_removes_keyword_index_too(tmp_path):
    with Grimoire(mount=tmp_path, embedder=FakeEmbedder()) as g:
        added = g.add(group_key="note", keyword_text="ephemeral phoenix")
        assert g.delete(added.id) is True
        assert g.keyword_search("phoenix") == []


# ---------- keyword_text ----------


def test_add_round_trips_keywords(tmp_path):
    with Grimoire(mount=tmp_path, embedder=FakeEmbedder()) as g:
        added = g.add(group_key="note", vector_text="hello", keyword_text="alpha beta")
        assert added.keyword_text == "alpha beta"
        fetched = g.get(added.id)
        assert fetched is not None
        assert fetched.keyword_text == "alpha beta"


def test_add_keywords_default_to_none(tmp_path):
    with Grimoire(mount=tmp_path, embedder=FakeEmbedder()) as g:
        added = g.add(group_key="note", vector_text="hello")
        assert added.keyword_text is None
        fetched = g.get(added.id)
        assert fetched is not None
        assert fetched.keyword_text is None


def test_add_empty_keyword_text_indexed_as_empty_string(tmp_path):
    # Empty string is distinct from None — preserve caller intent. An entry
    # with keyword_text="" lands in the FTS index but won't match any query.
    with Grimoire(mount=tmp_path, embedder=FakeEmbedder()) as g:
        added = g.add(group_key="note", vector_text="hello", keyword_text="")
        fetched = g.get(added.id)
        assert fetched is not None
        assert fetched.keyword_text == ""


def test_keyword_search_finds_entry_by_keyword_text(tmp_path):
    with Grimoire(mount=tmp_path, embedder=FakeEmbedder()) as g:
        g.add(
            group_key="note", vector_text="A bird sings at dawn", keyword_text="phoenix"
        )
        g.add(group_key="note", vector_text="Dragons fly at midnight")
        results = g.keyword_search("phoenix")
        assert len(results) == 1
        assert results[0].keyword_text == "phoenix"


def test_keyword_search_ignores_vector_text(tmp_path):
    """vector_text is NOT in the FTS index — only keyword_text is searchable."""
    with Grimoire(mount=tmp_path, embedder=FakeEmbedder()) as g:
        g.add(group_key="note", vector_text="phoenix soars at dawn")
        assert g.keyword_search("phoenix") == []


def test_delete_removes_keyword_index_entry_too(tmp_path):
    with Grimoire(mount=tmp_path, embedder=FakeEmbedder()) as g:
        added = g.add(
            group_key="note", vector_text="A bird sings", keyword_text="phoenix"
        )
        assert g.delete(added.id) is True
        assert g.keyword_search("phoenix") == []


def test_keywords_returned_by_list_and_search(tmp_path):
    with Grimoire(mount=tmp_path, embedder=FakeEmbedder()) as g:
        g.add(group_key="note", vector_text="hello", keyword_text="alpha")
        listed = g.list()
        assert listed[0].keyword_text == "alpha"
        # Also via vector_search
        v_results = g.vector_search("hello", k=1)
        assert v_results[0].keyword_text == "alpha"
        # And via keyword_search
        k_results = g.keyword_search("alpha")
        assert k_results[0].keyword_text == "alpha"


# ---------- add_many ----------


def test_add_many_round_trips(tmp_path):
    with Grimoire(mount=tmp_path, embedder=FakeEmbedder()) as g:
        records = [
            {"group_key": "note", "vector_text": "alpha"},
            {"group_key": "spell", "vector_text": "lumos"},
            {
                "group_key": "note",
                "vector_text": "beta",
                "keyword_text": "b",
                "payload": {"weight": 1},
                "threshold": 0.5,
            },
        ]
        added = g.add_many(records)
        assert [e.vector_text for e in added] == ["alpha", "lumos", "beta"]
        assert [e.group_key for e in added] == ["note", "spell", "note"]
        assert added[2].keyword_text == "b"
        assert added[2].payload == {"weight": 1}
        assert added[2].threshold == 0.5
        # Persisted: each entry is fetchable, searchable.
        for entry in added:
            fetched = g.get(entry.id)
            assert fetched is not None
            assert fetched.vector_text == entry.vector_text


def test_add_many_returns_empty_list_on_empty_input(tmp_path):
    with Grimoire(mount=tmp_path, embedder=FakeEmbedder()) as g:
        assert g.add_many([]) == []
        assert g.list() == []


def test_add_many_calls_embed_many_once(tmp_path):
    Grimoire(mount=tmp_path, embedder=CountingEmbedder()).close()

    e = CountingEmbedder()
    with Grimoire(mount=tmp_path, embedder=e) as g:
        e.embed_calls = 0
        e.embed_many_calls = 0
        records = [{"group_key": "note", "vector_text": f"e{i}"} for i in range(5)]
        g.add_many(records)
        # One batch call, not five single-embed calls.
        assert e.embed_many_calls == 1
        assert e.embed_calls == 0


def test_add_many_atomic_on_embed_failure(tmp_path):
    """If embedding fails, no records leak through partially."""

    class FailEmbedMany(FakeEmbedder):
        def embed_many(self, texts):
            raise RuntimeError("embed batch failed")

    with Grimoire(mount=tmp_path, embedder=FakeEmbedder()) as g:
        g.add(group_key="note", vector_text="existing")

    with Grimoire(mount=tmp_path, embedder=FailEmbedMany()) as g:
        with pytest.raises(RuntimeError, match="embed batch failed"):
            g.add_many([{"group_key": "note", "vector_text": "new"}])
        remaining = g.list()
        assert len(remaining) == 1
        assert remaining[0].vector_text == "existing"


def test_add_many_results_are_searchable(tmp_path):
    with Grimoire(mount=tmp_path, embedder=FakeEmbedder()) as g:
        g.add_many(
            [
                {"group_key": "note", "vector_text": "the moon is full"},
                {
                    "group_key": "note",
                    "vector_text": "dragons fly at midnight",
                    "keyword_text": "dragons midnight",
                },
            ]
        )
        v_results = g.vector_search("the moon is full", k=2)
        assert v_results[0].vector_text == "the moon is full"
        k_results = g.keyword_search("dragons")
        assert len(k_results) == 1


# ---------- thread sharing ----------


def test_init_default_is_thread_bound(tmp_path):
    """Pin: by default a Grimoire is bound to its constructing thread.

    Single-threaded scripts and the CLI rely on this safety rail. If the
    default ever flips, this test goes loud — making the change deliberate.
    """
    with Grimoire(mount=tmp_path, embedder=FakeEmbedder()) as g:
        errors: list[Exception] = []

        def worker() -> None:
            try:
                g.add(group_key="note", vector_text="x")
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
    with Grimoire(
        mount=tmp_path, embedder=FakeEmbedder(), check_same_thread=False
    ) as g:
        added: list[str] = []

        def worker() -> None:
            entry = g.add(
                group_key="note",
                vector_text="hello from worker",
                keyword_text="worker hello",
            )
            added.append(entry.id)

        t = threading.Thread(target=worker)
        t.start()
        t.join()
        assert len(added) == 1

        # The write is visible to the main thread, both via id lookup and search.
        fetched = g.get(added[0])
        assert fetched is not None
        assert fetched.vector_text == "hello from worker"
        results = g.vector_search("hello from worker", k=1)
        assert results[0].id == added[0]
        kw = g.keyword_search("worker")
        assert len(kw) == 1


def test_attach_check_same_thread_false_threads_through(tmp_path):
    """The kwarg must work on the attach codepath too, not just create."""
    Grimoire(mount=tmp_path, embedder=FakeEmbedder()).close()

    with Grimoire(
        mount=tmp_path, embedder=FakeEmbedder(), check_same_thread=False
    ) as g:
        added: list[str] = []

        def worker() -> None:
            added.append(
                g.add(group_key="note", vector_text="reopened cross-thread").id
            )

        t = threading.Thread(target=worker)
        t.start()
        t.join()
        assert len(added) == 1
        assert g.get(added[0]) is not None


# ---------- delete_many ----------


def test_delete_many_returns_empty_list_on_empty_input(tmp_path):
    with Grimoire(mount=tmp_path, embedder=FakeEmbedder()) as g:
        assert g.delete_many([]) == []


def test_delete_many_returns_bools_aligned_to_input(tmp_path):
    with Grimoire(mount=tmp_path, embedder=FakeEmbedder()) as g:
        a = g.add(group_key="note", vector_text="a")
        b = g.add(group_key="note", vector_text="b")
        results = g.delete_many([a.id, "01HXXXXXXXXXXXXXXXXXXXXXXX", b.id])
        assert results == [True, False, True]
        assert g.get(a.id) is None
        assert g.get(b.id) is None


def test_delete_many_cascades_to_vectors_and_fts(tmp_path):
    with Grimoire(mount=tmp_path, embedder=FakeEmbedder()) as g:
        a = g.add(group_key="note", vector_text="ephemeral phoenix")
        b = g.add(group_key="note", vector_text="another phoenix")
        g.delete_many([a.id, b.id])
        assert g.vector_search("phoenix", k=10) == []
        assert g.keyword_search("phoenix") == []


def test_delete_many_duplicate_ids_get_same_answer(tmp_path):
    with Grimoire(mount=tmp_path, embedder=FakeEmbedder()) as g:
        a = g.add(group_key="note", vector_text="hello")
        # Both occurrences should report True (existed at call time).
        results = g.delete_many([a.id, a.id])
        assert results == [True, True]
        assert g.get(a.id) is None


def test_delete_many_does_not_touch_unlisted_entries(tmp_path):
    with Grimoire(mount=tmp_path, embedder=FakeEmbedder()) as g:
        keep = g.add(group_key="note", vector_text="survivor")
        gone = g.add(group_key="note", vector_text="doomed")
        g.delete_many([gone.id])
        assert g.get(keep.id) is not None
        assert g.get(gone.id) is None


# ---------- update ----------
#
# update() is intentionally narrow: only `payload` and `threshold` are
# mutable. The indexed and identity fields (`vector_text`, `keyword_text`,
# `group_key`, `group_ref`) are immutable after creation. To change them,
# delete the entry and add a fresh one.


def test_update_returns_none_for_missing_id(tmp_path):
    with Grimoire(mount=tmp_path, embedder=FakeEmbedder()) as g:
        assert g.update("01HXXXXXXXXXXXXXXXXXXXXXXX", payload={"x": 1}) is None


def test_update_no_args_returns_unchanged_entry(tmp_path):
    with Grimoire(mount=tmp_path, embedder=FakeEmbedder()) as g:
        added = g.add(
            group_key="note",
            vector_text="hello",
            keyword_text="a",
            payload={"k": "v"},
        )
        result = g.update(added.id)
        assert result is not None
        assert result.id == added.id
        assert result.vector_text == "hello"
        assert result.keyword_text == "a"
        assert result.group_key == "note"
        assert result.payload == {"k": "v"}


def test_update_rejects_immutable_fields(tmp_path):
    """Passing an indexed/identity field is a TypeError — they're immutable."""
    with Grimoire(mount=tmp_path, embedder=FakeEmbedder()) as g:
        added = g.add(group_key="note", vector_text="hello")
        for kwarg in ("vector_text", "keyword_text", "group_key", "group_ref"):
            with pytest.raises(TypeError):
                g.update(added.id, **{kwarg: "x"})


def test_update_does_not_reembed_or_touch_fts(tmp_path):
    """Pin: payload/threshold updates never call the embedder or rewrite FTS."""
    Grimoire(mount=tmp_path, embedder=CountingEmbedder()).close()
    e = CountingEmbedder()
    with Grimoire(mount=tmp_path, embedder=e) as g:
        added = g.add(group_key="note", vector_text="hello", keyword_text="phoenix")
        e.embed_calls = 0
        e.embed_many_calls = 0
        result = g.update(added.id, payload={"foo": "bar"}, threshold=0.5)
        assert e.embed_calls == 0
        assert e.embed_many_calls == 0
        assert result is not None
        assert result.payload == {"foo": "bar"}
        assert result.threshold == 0.5
        # FTS and vector indexes untouched.
        assert g.keyword_search("phoenix")[0].id == added.id
        assert g.vector_search("hello", k=1)[0].id == added.id


def test_update_clears_payload_and_threshold_when_passed_none(tmp_path):
    with Grimoire(mount=tmp_path, embedder=FakeEmbedder()) as g:
        added = g.add(
            group_key="note",
            vector_text="hello",
            payload={"k": "v"},
            threshold=0.5,
        )
        updated = g.update(added.id, payload=None, threshold=None)
        assert updated is not None
        assert updated.payload is None
        assert updated.threshold is None
        # Persists across re-fetch.
        fetched = g.get(added.id)
        assert fetched is not None
        assert fetched.payload is None
        assert fetched.threshold is None


def test_update_omitted_field_is_preserved(tmp_path):
    """Pin: omitting a field must NOT clear it (the _UNSET sentinel job)."""
    with Grimoire(mount=tmp_path, embedder=FakeEmbedder()) as g:
        added = g.add(
            group_key="note",
            vector_text="hello",
            payload={"k": "v"},
            threshold=0.5,
        )
        # Patch only payload — threshold (and everything else) must survive.
        updated = g.update(added.id, payload={"k": "v2"})
        assert updated is not None
        assert updated.payload == {"k": "v2"}
        assert updated.threshold == 0.5


def test_update_persists_across_reopens(tmp_path):
    with Grimoire(mount=tmp_path, embedder=FakeEmbedder()) as g:
        added = g.add(group_key="note", vector_text="hello")
        g.update(added.id, payload={"v": 1}, threshold=0.25)

    with Grimoire(mount=tmp_path, embedder=FakeEmbedder()) as g:
        fetched = g.get(added.id)
        assert fetched is not None
        assert fetched.vector_text == "hello"
        assert fetched.payload == {"v": 1}
        assert fetched.threshold == 0.25


def test_update_preserves_id_and_created_at(tmp_path):
    """Updates must not reseat the entry's identity or its derived timestamp."""
    with Grimoire(mount=tmp_path, embedder=FakeEmbedder()) as g:
        added = g.add(group_key="note", vector_text="hello")
        before = added.created_at
        updated = g.update(added.id, payload={"v": 1})
        assert updated is not None
        assert updated.id == added.id
        assert updated.created_at == before


def test_add_many_assigns_distinct_ids_in_input_order(tmp_path):
    with Grimoire(mount=tmp_path, embedder=FakeEmbedder()) as g:
        added = g.add_many(
            [{"group_key": "note", "vector_text": f"e{i}"} for i in range(10)]
        )
        ids = [e.id for e in added]
        assert len(set(ids)) == 10
        # ULIDs sort lexicographically by creation time, and add_many assigns
        # them in input order — so the input ordering matches the id ordering.
        assert ids == sorted(ids)


# --- group_ref + nullable group_key -----------------------------------------


def test_add_persists_group_ref(tmp_path):
    with Grimoire(mount=tmp_path, embedder=FakeEmbedder()) as g:
        entry = g.add(group_key="doc", group_ref="path/to/file.md", vector_text="hello")
        assert entry.group_ref == "path/to/file.md"
        fetched = g.get(entry.id)
        assert fetched is not None and fetched.group_ref == "path/to/file.md"


def test_group_ref_unique_within_group_key(tmp_path):
    with Grimoire(mount=tmp_path, embedder=FakeEmbedder()) as g:
        g.add(group_key="doc", group_ref="r1", vector_text="a")
        with pytest.raises(sqlite3.IntegrityError):
            g.add(group_key="doc", group_ref="r1", vector_text="b")


def test_group_ref_same_value_allowed_across_group_keys(tmp_path):
    with Grimoire(mount=tmp_path, embedder=FakeEmbedder()) as g:
        a = g.add(group_key="doc", group_ref="r1", vector_text="a")
        b = g.add(group_key="page", group_ref="r1", vector_text="b")
        assert a.id != b.id


def test_group_ref_unique_in_global_namespace(tmp_path):
    """group_key=None still enforces uniqueness on group_ref alone."""
    with Grimoire(mount=tmp_path, embedder=FakeEmbedder()) as g:
        g.add(group_ref="g1", vector_text="a")
        with pytest.raises(sqlite3.IntegrityError):
            g.add(group_ref="g1", vector_text="b")


def test_group_ref_nulls_allowed_repeatedly(tmp_path):
    """SQLite treats NULLs as distinct; multiple entries without group_ref OK."""
    with Grimoire(mount=tmp_path, embedder=FakeEmbedder()) as g:
        a = g.add(group_key="doc", vector_text="a")
        b = g.add(group_key="doc", vector_text="b")
        assert a.id != b.id
        assert a.group_ref is None and b.group_ref is None


def test_get_by_group_ref_within_group(tmp_path):
    with Grimoire(mount=tmp_path, embedder=FakeEmbedder()) as g:
        added = g.add(group_key="doc", group_ref="r1", vector_text="hello")
        g.add(group_key="page", group_ref="r1", vector_text="other")
        found = g.get_by_group_ref(group_key="doc", group_ref="r1")
        assert found is not None and found.id == added.id


def test_get_by_group_ref_in_global_namespace(tmp_path):
    with Grimoire(mount=tmp_path, embedder=FakeEmbedder()) as g:
        added = g.add(group_ref="g1", vector_text="hello")
        g.add(group_key="doc", group_ref="g1", vector_text="other")
        found = g.get_by_group_ref(group_key=None, group_ref="g1")
        assert found is not None and found.id == added.id


def test_get_by_group_ref_returns_none_for_unknown(tmp_path):
    with Grimoire(mount=tmp_path, embedder=FakeEmbedder()) as g:
        assert g.get_by_group_ref(group_key="doc", group_ref="missing") is None


def test_nullable_group_key_in_add_and_search(tmp_path):
    with Grimoire(mount=tmp_path, embedder=FakeEmbedder()) as g:
        ungrouped = g.add(vector_text="lumos")
        grouped = g.add(group_key="spell", vector_text="lumos")
        assert ungrouped.group_key is None
        # No-filter search returns both.
        all_results = g.vector_search("lumos", k=10)
        ids = {r.id for r in all_results}
        assert ungrouped.id in ids and grouped.id in ids
        # group_key="spell" filter excludes the ungrouped one.
        spell_results = g.vector_search("lumos", group_key="spell", k=10)
        assert {r.id for r in spell_results} == {grouped.id}


def test_list_filters_by_group_ref(tmp_path):
    with Grimoire(mount=tmp_path, embedder=FakeEmbedder()) as g:
        a = g.add(group_key="doc", group_ref="r1", vector_text="a")
        g.add(group_key="doc", group_ref="r2", vector_text="b")
        b = g.add(group_key="page", group_ref="r1", vector_text="c")
        results = g.list(group_ref="r1")
        assert {r.id for r in results} == {a.id, b.id}


def test_list_filters_by_group_key_and_group_ref(tmp_path):
    with Grimoire(mount=tmp_path, embedder=FakeEmbedder()) as g:
        a = g.add(group_key="doc", group_ref="r1", vector_text="a")
        g.add(group_key="doc", group_ref="r2", vector_text="b")
        g.add(group_key="page", group_ref="r1", vector_text="c")
        results = g.list(group_key="doc", group_ref="r1")
        assert [r.id for r in results] == [a.id]

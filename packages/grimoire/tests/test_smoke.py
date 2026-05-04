import hashlib

import pytest
from grimoire import Entry, Grimoire, GrimoireMismatch, InvalidEmbedder


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


def test_open_creates_file_idempotently(tmp_path):
    db = tmp_path / "store.db"
    Grimoire.open(db, embedder=FakeEmbedder()).close()
    Grimoire.open(db, embedder=FakeEmbedder()).close()
    assert db.exists()


def test_embedder_model_mismatch_raises(tmp_path):
    db = tmp_path / "store.db"
    Grimoire.open(db, embedder=FakeEmbedder(model="alpha")).close()
    with pytest.raises(GrimoireMismatch):
        Grimoire.open(db, embedder=FakeEmbedder(model="beta"))


def test_embedder_dimension_mismatch_raises(tmp_path):
    db = tmp_path / "store.db"
    Grimoire.open(db, embedder=FakeEmbedder(dimension=8)).close()
    with pytest.raises(GrimoireMismatch):
        Grimoire.open(db, embedder=FakeEmbedder(dimension=16))


def test_add_returns_entry(tmp_path):
    with Grimoire.open(tmp_path / "store.db", embedder=FakeEmbedder()) as g:
        entry = g.add(kind="note", content="the moon is full")
        assert isinstance(entry, Entry)
        assert entry.kind == "note"
        assert entry.content == "the moon is full"


def test_search_finds_exact_match_first(tmp_path):
    with Grimoire.open(tmp_path / "store.db", embedder=FakeEmbedder()) as g:
        g.add(kind="note", content="the moon is full")
        g.add(kind="note", content="dragons fly at midnight")
        g.add(kind="note", content="potions bubble in the cauldron")

        results = g.search("the moon is full", k=3)
        assert len(results) == 3
        assert results[0].content == "the moon is full"
        assert results[0].distance == 0.0


def test_search_filters_by_kind(tmp_path):
    with Grimoire.open(tmp_path / "store.db", embedder=FakeEmbedder()) as g:
        g.add(kind="spell", content="lumos")
        g.add(kind="potion", content="lumos")

        results = g.search("lumos", kind="spell", k=10)
        assert len(results) == 1
        assert results[0].kind == "spell"


def test_dynamic_threshold_drops_low_match(tmp_path):
    with Grimoire.open(tmp_path / "store.db", embedder=FakeEmbedder()) as g:
        g.add(kind="note", content="the moon is full", threshold=0.0)
        g.add(kind="note", content="dragons fly at midnight", threshold=0.0)

        all_results = g.search("the moon is full", k=10)
        assert len(all_results) == 2

        gated = g.search("the moon is full", k=10, dynamic_threshold=True)
        assert len(gated) == 1
        assert gated[0].content == "the moon is full"


def test_two_files_are_independent(tmp_path):
    a_path = tmp_path / "a.db"
    b_path = tmp_path / "b.db"
    with Grimoire.open(a_path, embedder=FakeEmbedder()) as a:
        a.add(kind="note", content="alpha")
    with Grimoire.open(b_path, embedder=FakeEmbedder()) as b:
        b.add(kind="note", content="beta")
        results = b.search("alpha", k=10)
        assert all(r.content != "alpha" for r in results)


def test_data_persists_across_reopens(tmp_path):
    db = tmp_path / "store.db"
    with Grimoire.open(db, embedder=FakeEmbedder()) as g:
        g.add(kind="note", content="the moon is full")

    with Grimoire.open(db, embedder=FakeEmbedder()) as g:
        results = g.search("the moon is full", k=1)
        assert len(results) == 1
        assert results[0].content == "the moon is full"


def test_get_returns_entry(tmp_path):
    with Grimoire.open(tmp_path / "store.db", embedder=FakeEmbedder()) as g:
        added = g.add(kind="note", content="lumos")
        fetched = g.get(added.id)
        assert fetched is not None
        assert fetched.id == added.id
        assert fetched.content == "lumos"


def test_get_returns_none_for_missing_id(tmp_path):
    with Grimoire.open(tmp_path / "store.db", embedder=FakeEmbedder()) as g:
        assert g.get("01HXXXXXXXXXXXXXXXXXXXXXXX") is None


def test_list_returns_all_entries_in_chronological_order(tmp_path):
    with Grimoire.open(tmp_path / "store.db", embedder=FakeEmbedder()) as g:
        a = g.add(kind="note", content="first")
        b = g.add(kind="note", content="second")
        c = g.add(kind="note", content="third")
        results = g.list()
        assert [r.id for r in results] == [a.id, b.id, c.id]


def test_list_filters_by_kind(tmp_path):
    with Grimoire.open(tmp_path / "store.db", embedder=FakeEmbedder()) as g:
        g.add(kind="spell", content="lumos")
        g.add(kind="potion", content="felix felicis")
        g.add(kind="spell", content="alohomora")

        spells = g.list(kind="spell")
        assert len(spells) == 2
        assert all(r.kind == "spell" for r in spells)


def test_list_paginates_via_after_id(tmp_path):
    with Grimoire.open(tmp_path / "store.db", embedder=FakeEmbedder()) as g:
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
    with Grimoire.open(tmp_path / "store.db", embedder=FakeEmbedder()) as g:
        for i in range(5):
            g.add(kind="note", content=f"e{i}")
        assert len(g.list(limit=3)) == 3


def test_delete_removes_entry_and_vector(tmp_path):
    with Grimoire.open(tmp_path / "store.db", embedder=FakeEmbedder()) as g:
        added = g.add(kind="note", content="ephemeral")
        assert g.delete(added.id) is True
        assert g.get(added.id) is None

        # Search should no longer return it.
        results = g.search("ephemeral", k=10)
        assert all(r.id != added.id for r in results)


def test_delete_returns_false_for_missing_id(tmp_path):
    with Grimoire.open(tmp_path / "store.db", embedder=FakeEmbedder()) as g:
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
        Grimoire.open(db, embedder=_BadDimensionEmbedder())
    assert not db.exists() or db.stat().st_size == 0


def test_embedder_with_zero_dimension_rejected(tmp_path):
    with pytest.raises(InvalidEmbedder):
        Grimoire.open(tmp_path / "store.db", embedder=_NonPositiveDimensionEmbedder())


def test_embedder_with_empty_model_rejected(tmp_path):
    with pytest.raises(InvalidEmbedder):
        Grimoire.open(tmp_path / "store.db", embedder=_EmptyModelEmbedder())

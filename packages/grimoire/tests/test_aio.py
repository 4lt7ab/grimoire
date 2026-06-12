import asyncio

import pytest
from grimoire.aio import AsyncGrimoire
from grimoire.data.entry import Entry, Filters
from grimoire.errors import EmbedderRequired


async def test_open_and_crud_round_trip(tmp_path, fake_embedder):
    g = await AsyncGrimoire.open(tmp_path / "g.db", embedder=fake_embedder)
    [e] = await g.add([Entry(uniq_id=None, data={"k": "v"})])
    assert e.uniq_id is not None
    [fetched] = await g.get([e.uniq_id])
    assert fetched.data == {"k": "v"}
    assert await g.remove([e.uniq_id]) == [e.uniq_id]
    assert await g.get([e.uniq_id]) == []


async def test_index_query_match_search(tmp_path, fake_embedder):
    g = await AsyncGrimoire.open(tmp_path / "g.db", embedder=fake_embedder)
    [e] = await g.add([Entry(None, {"name": "phoenix"})])
    await g.index(
        e.uniq_id,
        ref="phoenix-001",
        ord=("creature", None, None, None, None),
        match="phoenix fire-bird ashes",
        search="a solar phoenix reborn from ashes",
    )

    entries, indexes = await g.query(Filters(equals={"ordinal_1": ["creature"]}))
    assert [x.uniq_id for x in entries] == [e.uniq_id]
    assert indexes[0].uniq_ref == "phoenix-001"

    entries, _ = await g.fetch(["phoenix-001"])
    assert [x.uniq_id for x in entries] == [e.uniq_id]

    entries, hits = await g.match('"phoenix"')
    assert [x.uniq_id for x in entries] == [e.uniq_id]
    assert hits[0].score >= 0

    entries, hits = await g.search("reborn bird")
    assert e.uniq_id in {x.uniq_id for x in entries}


async def test_context_manager_commits(tmp_path, fake_embedder):
    path = tmp_path / "g.db"
    async with await AsyncGrimoire.open(path, embedder=fake_embedder) as g:
        [e] = await g.add([Entry(None, {"k": "v"})])

    async with await AsyncGrimoire.open(path, embedder=fake_embedder) as g:
        [fetched] = await g.get([e.uniq_id])
        assert fetched.data == {"k": "v"}


async def test_context_manager_rolls_back_on_error(tmp_path, fake_embedder):
    path = tmp_path / "g.db"
    with pytest.raises(RuntimeError):
        async with await AsyncGrimoire.open(path, embedder=fake_embedder) as g:
            [e] = await g.add([Entry(None, {"k": "v"})])
            raise RuntimeError("boom")

    async with await AsyncGrimoire.open(path, embedder=fake_embedder) as g:
        assert await g.get([e.uniq_id]) == []


async def test_search_without_embedder_raises(tmp_path):
    g = await AsyncGrimoire.open(tmp_path / "g.db")
    with pytest.raises(EmbedderRequired):
        await g.search("anything")


async def test_peek_reports_counts(tmp_path, fake_embedder):
    path = tmp_path / "g.db"
    async with await AsyncGrimoire.open(path, embedder=fake_embedder) as g:
        await g.add([Entry(None, {"k": "v"})])

    peeked = await AsyncGrimoire.peek(path)
    assert peeked.model == "fake"
    assert peeked.entry_count == 1


async def test_concurrent_writes_serialize(tmp_path, fake_embedder):
    g = await AsyncGrimoire.open(tmp_path / "g.db", embedder=fake_embedder)
    results = await asyncio.gather(
        *(g.add([Entry(None, {"i": i})]) for i in range(20))
    )
    ids = {r[0].uniq_id for r in results}
    assert len(ids) == 20
    entries, _ = await g.query(limit=100)
    assert len(entries) == 0  # no entry_idx rows written
    assert len(await g.get(list(ids))) == 20

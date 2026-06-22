import asyncio

import pytest
from fastmcp import Client
from grimoire_cli.cli import app
from grimoire_cli.mcp import build_server
from grimoire_cli.mount import ENV_VAR, resolve
from typer.testing import CliRunner

runner = CliRunner()


@pytest.fixture
def mounted(tmp_path, monkeypatch, patched_embedder):
    monkeypatch.setenv(ENV_VAR, str(tmp_path))
    runner.invoke(app, ["mount", "create"])
    return tmp_path


@pytest.fixture
def server(mounted):
    return build_server(resolve(mounted))


def _run(coro):
    return asyncio.run(coro)


def test_server_registers_expected_tools(server):
    async def _list():
        async with Client(server) as client:
            return [t.name for t in await client.list_tools()]

    names = set(_run(_list()))
    assert names == {
        "info",
        "add",
        "update",
        "get",
        "remove",
        "query",
        "fetch",
        "match",
        "search",
    }


def test_add_then_get_roundtrip(server):
    async def _go():
        async with Client(server) as client:
            created = await client.call_tool("add", {"data": {"k": "v"}})
            uniq_id = created.data["uniq_id"]
            fetched = await client.call_tool("get", {"uniq_ids": [uniq_id]})
            return uniq_id, fetched.data

    uniq_id, fetched = _run(_go())
    assert len(fetched) == 1
    assert fetched[0]["uniq_id"] == uniq_id
    assert fetched[0]["data"] == {"k": "v"}


def test_add_with_index_kwargs_writes_sidecars(server):
    async def _go():
        async with Client(server) as client:
            created = await client.call_tool(
                "add",
                {
                    "data": {"k": "v"},
                    "ref": "book-1",
                    "ord": [1954.0, None, None, "novel", None],
                    "match": "phoenix",
                    "search": "an epic quest",
                },
            )
            uniq_id = created.data["uniq_id"]
            fetched = await client.call_tool("fetch", {"uniq_refs": ["book-1"]})
            kw_hits = await client.call_tool("match", {"query": "phoenix"})
            return uniq_id, fetched.data, kw_hits.data

    uniq_id, fetched, kw_hits = _run(_go())
    assert fetched[0]["entry"]["uniq_id"] == uniq_id
    assert fetched[0]["index"]["ordinal_4"] == "novel"
    assert fetched[0]["index"]["ordinal_1"] == 1954.0
    assert any(h["entry"]["uniq_id"] == uniq_id for h in kw_hits)


def test_add_with_owner_filters_by_owner(server):
    async def _go():
        async with Client(server) as client:
            created = await client.call_tool(
                "add", {"data": {"k": "v"}, "ref": "book-1", "owner": "user-42"}
            )
            uniq_id = created.data["uniq_id"]
            pairs = await client.call_tool(
                "query", {"equals": {"owner_ref": ["user-42"]}}
            )
            return uniq_id, pairs.data

    uniq_id, pairs = _run(_go())
    assert [p["entry"]["uniq_id"] for p in pairs] == [uniq_id]
    assert pairs[0]["index"]["owner_ref"] == "user-42"


def test_update_replaces_data(server):
    async def _go():
        async with Client(server) as client:
            created = await client.call_tool("add", {"data": {"v": 1}})
            uniq_id = created.data["uniq_id"]
            updated = await client.call_tool(
                "update", {"uniq_id": uniq_id, "data": {"v": 2}}
            )
            return updated.data

    updated = _run(_go())
    assert updated["data"] == {"v": 2}


def test_update_unknown_id_errors(server):
    async def _go():
        async with Client(server) as client:
            await client.call_tool(
                "update",
                {"uniq_id": "01MISSINGMISSINGMISSINGMI", "data": {}},
            )

    with pytest.raises(Exception, match="No entry"):
        _run(_go())


def test_update_idx_put_replaces_idx(server):
    async def _go():
        async with Client(server) as client:
            created = await client.call_tool("add", {})
            uniq_id = created.data["uniq_id"]
            await client.call_tool(
                "update",
                {"uniq_id": uniq_id, "ref": "X", "ord": [None, None, None, "a", "b"]},
            )
            await client.call_tool("update", {"uniq_id": uniq_id, "ref": "Y"})
            pairs = await client.call_tool("query", {})
            return pairs.data

    pairs = _run(_go())
    assert pairs[0]["index"]["uniq_ref"] == "Y"
    assert pairs[0]["index"]["ordinal_4"] is None


def test_update_rejects_bad_ord_length(server):
    async def _go():
        async with Client(server) as client:
            created = await client.call_tool("add", {})
            uniq_id = created.data["uniq_id"]
            await client.call_tool(
                "update", {"uniq_id": uniq_id, "ord": [1.0, 2.0, 3.0, 4.0]}
            )

    with pytest.raises(Exception, match="ord"):
        _run(_go())


def test_update_combined_data_and_idx(server):
    async def _go():
        async with Client(server) as client:
            created = await client.call_tool("add", {"data": {"v": 1}})
            uniq_id = created.data["uniq_id"]
            updated = await client.call_tool(
                "update",
                {"uniq_id": uniq_id, "data": {"v": 2}, "ref": "X"},
            )
            pairs = await client.call_tool("query", {})
            return updated.data, pairs.data

    updated, pairs = _run(_go())
    assert updated["data"] == {"v": 2}
    assert pairs[0]["index"]["uniq_ref"] == "X"


def test_update_data_only_leaves_idx_alone(server):
    async def _go():
        async with Client(server) as client:
            created = await client.call_tool("add", {"data": {"v": 1}, "ref": "X"})
            uniq_id = created.data["uniq_id"]
            await client.call_tool("update", {"uniq_id": uniq_id, "data": {"v": 2}})
            pairs = await client.call_tool("query", {})
            return pairs.data

    pairs = _run(_go())
    assert pairs[0]["entry"]["data"] == {"v": 2}
    assert pairs[0]["index"]["uniq_ref"] == "X"


def test_remove_returns_ids(server):
    async def _go():
        async with Client(server) as client:
            created = await client.call_tool("add", {})
            uniq_id = created.data["uniq_id"]
            removed = await client.call_tool("remove", {"uniq_ids": [uniq_id]})
            return uniq_id, removed.data

    uniq_id, removed = _run(_go())
    assert removed == [uniq_id]


def test_remove_missing_returns_empty_list(server):
    async def _go():
        async with Client(server) as client:
            removed = await client.call_tool(
                "remove", {"uniq_ids": ["01MISSINGMISSINGMISSINGMI"]}
            )
            return removed.data

    removed = _run(_go())
    assert removed == []


def test_query_filters_by_equals(server):
    async def _go():
        async with Client(server) as client:
            await client.call_tool(
                "add", {"ref": "a", "ord": ["alpha", None, None, None, None]}
            )
            await client.call_tool(
                "add", {"ref": "b", "ord": ["beta", None, None, None, None]}
            )
            pairs = await client.call_tool(
                "query", {"equals": {"ordinal_1": ["alpha"]}}
            )
            return pairs.data

    pairs = _run(_go())
    assert [p["index"]["uniq_ref"] for p in pairs] == ["a"]


def test_fetch_by_uniq_ref(server):
    async def _go():
        async with Client(server) as client:
            await client.call_tool("add", {"data": {"k": "v"}, "ref": "book-1"})
            pairs = await client.call_tool("fetch", {"uniq_refs": ["book-1"]})
            return pairs.data

    pairs = _run(_go())
    assert len(pairs) == 1
    assert pairs[0]["entry"]["data"] == {"k": "v"}


def test_match_returns_score(server):
    async def _go():
        async with Client(server) as client:
            created = await client.call_tool("add", {"match": "phoenix arcane ember"})
            uniq_id = created.data["uniq_id"]
            hits = await client.call_tool("match", {"query": "phoenix"})
            return uniq_id, hits.data

    uniq_id, hits = _run(_go())
    assert any(h["entry"]["uniq_id"] == uniq_id for h in hits)
    assert all("score" in h for h in hits)


def test_search_returns_distance(server):
    async def _go():
        async with Client(server) as client:
            created = await client.call_tool("add", {"search": "phoenix"})
            uniq_id = created.data["uniq_id"]
            hits = await client.call_tool("search", {"query": "phoenix"})
            return uniq_id, hits.data

    uniq_id, hits = _run(_go())
    assert any(h["entry"]["uniq_id"] == uniq_id for h in hits)
    assert all("distance" in h for h in hits)


def test_info_reports_per_table_counts(server):
    async def _go():
        async with Client(server) as client:
            await client.call_tool("add", {"ref": "r", "match": "text"})
            info = await client.call_tool("info", {})
            return info.data

    info = _run(_go())
    assert info["entry_count"] == 1
    assert info["entry_idx_count"] == 1
    assert info["entry_fts_count"] == 1
    assert info["entry_vec_count"] == 0

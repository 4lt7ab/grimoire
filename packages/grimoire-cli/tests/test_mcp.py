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
        "fetch",
        "entry_get",
        "entry_add",
        "entry_update",
        "entry_delete",
        "search_keyword",
        "search_semantic",
    }


def test_entry_add_then_fetch_roundtrip(server):
    async def _go():
        async with Client(server) as client:
            created = await client.call_tool(
                "entry_add",
                {
                    "group_key": "notes",
                    "group_ref": "note-001",
                    "context": "from chapter 3",
                    "payload": {"author": "merlin"},
                },
            )
            entry_id = created.data["id"]

            fetched = await client.call_tool("entry_get", {"entry_id": entry_id})
            return created.data, fetched.data

    created, fetched = _run(_go())
    assert fetched == created
    assert fetched["group_key"] == "notes"
    assert fetched["group_ref"] == "note-001"
    assert fetched["payload"] == {"author": "merlin"}
    assert fetched["context"] == "from chapter 3"


def test_entry_add_with_keyword_text_indexes_for_search(server):
    async def _go():
        async with Client(server) as client:
            created = await client.call_tool(
                "entry_add", {"keyword_text": "phoenix arcane ember"}
            )
            entry_id = created.data["id"]

            hits = await client.call_tool("search_keyword", {"query": "phoenix"})
            return entry_id, hits.data

    entry_id, hits = _run(_go())
    assert len(hits) == 1
    assert hits[0]["entry"]["id"] == entry_id
    assert hits[0]["keyword_text"] == "phoenix arcane ember"


def test_entry_add_with_semantic_text_embeds_for_search(server):
    async def _go():
        async with Client(server) as client:
            created = await client.call_tool(
                "entry_add", {"semantic_text": "a solar phoenix reborn"}
            )
            entry_id = created.data["id"]

            hits = await client.call_tool(
                "search_semantic", {"query": "creatures from ashes"}
            )
            return entry_id, hits.data

    entry_id, hits = _run(_go())
    assert any(h["entry"]["id"] == entry_id for h in hits)
    assert any(h["semantic_text"] == "a solar phoenix reborn" for h in hits)


def test_entry_update_replaces_keyword_text(server):
    async def _go():
        async with Client(server) as client:
            created = await client.call_tool("entry_add", {"keyword_text": "moon"})
            entry_id = created.data["id"]
            await client.call_tool(
                "entry_update",
                {"entry_id": entry_id, "keyword_text": "stars"},
            )
            moon = await client.call_tool("search_keyword", {"query": "moon"})
            stars = await client.call_tool("search_keyword", {"query": "stars"})
            return entry_id, moon.data, stars.data

    entry_id, moon, stars = _run(_go())
    assert moon == []
    assert any(h["entry"]["id"] == entry_id for h in stars)


def test_entry_update_without_text_preserves_index(server):
    async def _go():
        async with Client(server) as client:
            created = await client.call_tool("entry_add", {"keyword_text": "moon glow"})
            entry_id = created.data["id"]
            await client.call_tool(
                "entry_update", {"entry_id": entry_id, "context": "new ctx"}
            )
            hits = await client.call_tool("search_keyword", {"query": "moon"})
            return entry_id, hits.data

    entry_id, hits = _run(_go())
    assert any(
        h["entry"]["id"] == entry_id and h["keyword_text"] == "moon glow" for h in hits
    )


def test_entry_update_partial_preserves_unspecified_fields(server):
    async def _go():
        async with Client(server) as client:
            created = await client.call_tool(
                "entry_add",
                {"group_key": "notes", "context": "original context"},
            )
            entry_id = created.data["id"]

            updated = await client.call_tool(
                "entry_update",
                {"entry_id": entry_id, "context": "new context"},
            )
            return updated.data

    updated = _run(_go())
    assert updated["context"] == "new context"
    assert updated["group_key"] == "notes"


def test_entry_delete_idempotent(server):
    async def _go():
        async with Client(server) as client:
            first = await client.call_tool(
                "entry_delete", {"entry_id": "01HZZZZZZZZZZZZZZZZZZZZZZZ"}
            )
            return first.data

    result = _run(_go())
    assert result == {"id": "01HZZZZZZZZZZZZZZZZZZZZZZZ", "deleted": False}

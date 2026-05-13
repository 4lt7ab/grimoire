"""MCP server surface for a grimoire mount.

Mirrors the CLI's read+write commands as FastMCP tools, scoped to a single
mount picked at server boot. Mount administration (create/destroy/add/remove)
stays CLI-only; the MCP surface operates on existing databases.
"""

import re
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any

from fastmcp import FastMCP
from grimoire import grimoire
from grimoire.data.entry import Entry, Filters

from grimoire_cli import embed, mount


def _resolve_db(mnt: mount.Mount, name: str | None) -> Path:
    db_path = mnt.db_path(name)
    if not db_path.exists():
        target = f"database {name!r}" if name else "default database"
        raise ValueError(f"No {target} in the mount.")
    return db_path


def _human_size(n: int) -> str:
    if n < 1024:
        return f"{n} B"
    size = float(n)
    for unit in ("KB", "MB", "GB", "TB"):  # noqa: B007
        size /= 1024
        if size < 1024:
            break
    if size == int(size):
        return f"{int(size)} {unit}"
    return f"{size:.1f} {unit}"


def build_server(mnt: mount.Mount) -> FastMCP:
    """Construct a FastMCP server exposing the grimoire mount's read+write surface."""
    mcp = FastMCP("grimoire")

    @mcp.tool
    def info(name: str | None = None) -> dict[str, Any]:
        """Show metadata for a grimoire database.

        Includes embedder lock, schema version, entry counts, and file size.
        `name` selects a named DB in the mount; omit for the default DB.
        """
        db_path = _resolve_db(mnt, name)
        peeked = grimoire.peek(db_path)
        size_bytes = db_path.stat().st_size
        return {
            "name": name,
            "path": str(db_path),
            "size_bytes": size_bytes,
            "size": _human_size(size_bytes),
            **asdict(peeked),
        }

    @mcp.tool
    def fetch(
        name: str | None = None,
        ids: list[str] | None = None,
        group_keys: list[str] | None = None,
        group_refs: list[str] | None = None,
        cursor: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Fetch entries matching the given filters, ordered chronologically by id.

        For paging, pass `cursor` set to the last entry's id from the previous
        page. ULIDs sort lexicographically by creation time, so cursor paging
        walks entries in the order they were added.
        """
        db_path = _resolve_db(mnt, name)
        filters = Filters(
            id=ids or None,
            group_key=group_keys or None,
            group_ref=group_refs or None,
        )
        with grimoire.open(db_path, embedder=embed.build_embedder(mnt.models_dir)) as g:
            entries = g.fetch(filters, limit=limit, cursor=cursor)
        return [asdict(e) for e in entries]

    @mcp.tool
    def entry_get(entry_id: str, name: str | None = None) -> dict[str, Any]:
        """Fetch a single grimoire entry by id."""
        db_path = _resolve_db(mnt, name)
        with grimoire.open(db_path, embedder=embed.build_embedder(mnt.models_dir)) as g:
            entries = g.fetch(Filters(id=[entry_id]), limit=1)
        if not entries:
            raise ValueError(f"No entry with id {entry_id!r}.")
        return asdict(entries[0])

    @mcp.tool
    def entry_add(
        name: str | None = None,
        group_key: str | None = None,
        group_ref: str | None = None,
        context: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Create a grimoire entry.

        Add searchable text via `index_keyword` or `index_semantic` after
        creation; this tool only writes the entry record itself.
        """
        db_path = _resolve_db(mnt, name)
        with grimoire.open(db_path, embedder=embed.build_embedder(mnt.models_dir)) as g:
            [created] = g.add([Entry(
                id=None,
                group_key=group_key,
                group_ref=group_ref,
                payload=payload,
                context=context,
            )])
        return asdict(created)

    @mcp.tool
    def entry_update(
        entry_id: str,
        name: str | None = None,
        group_key: str | None = None,
        group_ref: str | None = None,
        payload: dict[str, Any] | None = None,
        context: str | None = None,
        put: bool = False,
    ) -> dict[str, Any]:
        """Update group_key, group_ref, payload, and context on an entry.

        Default mode is partial-update: unspecified fields are preserved. Pass
        `put=True` to switch to replace mode, where any field not supplied is
        set to NULL.

        To change keyword thresholds or semantic thresholds, re-run
        `index_keyword` or `index_semantic` with the new threshold value.
        """
        db_path = _resolve_db(mnt, name)
        with grimoire.open(db_path, embedder=embed.build_embedder(mnt.models_dir)) as g:
            existing = g.fetch(Filters(id=[entry_id]), limit=1)
            if not existing:
                raise ValueError(f"No entry with id {entry_id!r}.")
            current = existing[0]

            if put:
                merged = Entry(
                    id=current.id,
                    group_key=group_key,
                    group_ref=group_ref,
                    payload=payload,
                    context=context,
                )
            else:
                merged = replace(
                    current,
                    group_key=current.group_key if group_key is None else group_key,
                    group_ref=current.group_ref if group_ref is None else group_ref,
                    payload=current.payload if payload is None else payload,
                    context=current.context if context is None else context,
                )

            [returned] = g.update([merged])
        return asdict(returned)

    @mcp.tool
    def entry_delete(entry_id: str, name: str | None = None) -> dict[str, Any]:
        """Delete a grimoire entry by id.

        Idempotent — a missing id returns `deleted=false`.
        """
        db_path = _resolve_db(mnt, name)
        with grimoire.open(db_path, embedder=embed.build_embedder(mnt.models_dir)) as g:
            removed = g.remove([entry_id])
        return {"id": entry_id, "deleted": bool(removed)}

    @mcp.tool
    def index_keyword(
        entry_id: str,
        name: str | None = None,
        text: str | None = None,
        threshold_rank: float | None = None,
        delete: bool = False,
    ) -> dict[str, Any]:
        """Index, re-index, or remove an entry's keyword text in FTS5.

        Pass `text` to (re-)index, or `delete=True` to remove. The entry itself
        is not affected by `delete`; only the FTS5 row is dropped.
        """
        if delete and (text is not None or threshold_rank is not None):
            raise ValueError(
                "`delete` cannot be combined with `text` or `threshold_rank`."
            )
        if not delete and text is None:
            raise ValueError("Provide `text` to index, or `delete=True` to remove.")

        db_path = _resolve_db(mnt, name)
        with grimoire.open(db_path, embedder=embed.build_embedder(mnt.models_dir)) as g:
            if delete:
                removed = g.keyword_remove([entry_id])
                return {"id": entry_id, "deleted": bool(removed)}
            [indexed] = g.keyword([(entry_id, text)], threshold_rank=threshold_rank)
            return {
                "entry": asdict(indexed),
                "keyword_text": text,
                "threshold_rank": threshold_rank,
            }

    @mcp.tool
    def index_semantic(
        entry_id: str,
        name: str | None = None,
        text: str | None = None,
        partition: str | None = None,
        threshold_distance: float | None = None,
        delete: bool = False,
    ) -> dict[str, Any]:
        """Embed, re-embed, or remove an entry's semantic vector.

        Pass `text` to (re-)embed, or `delete=True` to remove. The entry itself
        is not affected by `delete`; only the vec row is dropped.
        """
        if delete and (
            text is not None or partition is not None or threshold_distance is not None
        ):
            raise ValueError(
                "`delete` cannot be combined with `text`, `partition`, "
                "or `threshold_distance`."
            )
        if not delete and text is None:
            raise ValueError("Provide `text` to embed, or `delete=True` to remove.")

        db_path = _resolve_db(mnt, name)
        with grimoire.open(db_path, embedder=embed.build_embedder(mnt.models_dir)) as g:
            if delete:
                removed = g.embed_remove([entry_id])
                return {"id": entry_id, "deleted": bool(removed)}
            [embedded] = g.embed(
                [(entry_id, text)],
                partition=partition,
                threshold_distance=threshold_distance,
            )
            return {
                "entry": asdict(embedded),
                "semantic_text": text,
                "partition": partition,
                "threshold_distance": threshold_distance,
            }

    @mcp.tool
    def search_keyword(
        query: str,
        name: str | None = None,
        group_keys: list[str] | None = None,
        group_refs: list[str] | None = None,
        ids: list[str] | None = None,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        """Keyword search via FTS5 BM25.

        Supports filtering by group_key, group_ref, and id. `rank` is the BM25
        score (higher = better, non-negative).
        """
        db_path = _resolve_db(mnt, name)
        filters = Filters(
            id=ids or None,
            group_key=group_keys or None,
            group_ref=group_refs or None,
        )
        # Quote-wrap each word token so apostrophes, punctuation, and bareword
        # FTS5 operators (AND/OR/NOT/NEAR/*) can't reach the parser. Join with
        # OR so casual prose matches any-of, not all-of; BM25 still ranks by
        # aggregate match strength.
        fts_query = " OR ".join(f'"{t}"' for t in re.findall(r"\w+", query))
        if not fts_query:
            return []

        with grimoire.open(db_path, embedder=embed.build_embedder(mnt.models_dir)) as g:
            hits = g.keyword_search(fts_query, filters=filters, limit=limit)
        return [
            {
                "entry": asdict(h.entry),
                "keyword_text": h.keyword_text,
                "threshold_rank": h.threshold_rank,
                "rank": h.score,
            }
            for h in hits
        ]

    @mcp.tool
    def search_semantic(
        query: str,
        name: str | None = None,
        partition: str | None = None,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        """Semantic search via vec0 KNN, narrowable by partition.

        `distance` is the raw vector distance (lower = better, non-negative).
        """
        db_path = _resolve_db(mnt, name)
        with grimoire.open(db_path, embedder=embed.build_embedder(mnt.models_dir)) as g:
            hits = g.semantic_search(query, partition=partition, limit=limit)
        return [
            {
                "entry": asdict(h.entry),
                "semantic_text": h.semantic_text,
                "threshold_distance": h.threshold_distance,
                "distance": h.distance,
            }
            for h in hits
        ]

    return mcp

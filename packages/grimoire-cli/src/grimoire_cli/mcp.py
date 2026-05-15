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
from grimoire.data.entry import Entry, Filters
from grimoire.grimoire import Grimoire

from grimoire_cli import embed, mount


def _resolve_db(mnt: mount.Mount, db: str | None) -> Path:
    db_path = mnt.db_path(db)
    if not db_path.exists():
        target = f"database {db!r}" if db else "default database"
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
    def info(db: str | None = None) -> dict[str, Any]:
        """Show metadata for a grimoire database.

        Includes embedder lock, schema version, entry counts, and file size.
        `db` selects a named DB in the mount; omit for the default DB.
        """
        db_path = _resolve_db(mnt, db)
        peeked = Grimoire.peek(db_path)
        size_bytes = db_path.stat().st_size
        return {
            "db": db,
            "path": str(db_path),
            "size_bytes": size_bytes,
            "size": _human_size(size_bytes),
            **asdict(peeked),
        }

    @mcp.tool
    def fetch(
        db: str | None = None,
        ids: list[str] | None = None,
        group_keys: list[str] | None = None,
        group_refs: list[str] | None = None,
        ordinal_gte: float | None = None,
        ordinal_lte: float | None = None,
        cursor: str | None = None,
        order_by: str = "id",
        descending: bool = False,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Fetch entries matching the given filters.

        Each entry carries its FTS5 (`keyword_text`, `threshold_rank`) and
        vec0 (`semantic_text`, `partition`, `threshold_distance`) index
        fields inline — null when the entry isn't indexed on that side.
        Entries also carry their `ordinal` (a consumer-supplied numeric sort
        key) inline.

        Default order is by id (chronological); pass `order_by="ordinal"` to
        sort by the ordinal column. NULL ordinals sort last. `descending`
        reverses direction.

        `cursor` is id-based; pair it with the default id-ascending order.
        For ordinal-window paging, use `ordinal_gte` / `ordinal_lte`.
        """
        if order_by not in {"id", "ordinal"}:
            raise ValueError("`order_by` must be 'id' or 'ordinal'.")

        db_path = _resolve_db(mnt, db)
        filters = Filters(
            id=ids or None,
            group_key=group_keys or None,
            group_ref=group_refs or None,
            ordinal_gte=ordinal_gte,
            ordinal_lte=ordinal_lte,
        )
        with Grimoire.open(db_path, embedder=embed.build_embedder(mnt.models_dir)) as g:
            entries = g.fetch(
                filters,
                limit=limit,
                cursor=cursor,
                order_by=order_by,  # type: ignore[arg-type]
                descending=descending,
            )
        return [asdict(e) for e in entries]

    @mcp.tool
    def entry_get(entry_id: str, db: str | None = None) -> dict[str, Any]:
        """Fetch a single grimoire entry by id."""
        db_path = _resolve_db(mnt, db)
        with Grimoire.open(db_path, embedder=embed.build_embedder(mnt.models_dir)) as g:
            entries = g.fetch(Filters(id=[entry_id]), limit=1)
        if not entries:
            raise ValueError(f"No entry with id {entry_id!r}.")
        return asdict(entries[0])

    @mcp.tool
    def entry_add(
        db: str | None = None,
        group_key: str | None = None,
        group_ref: str | None = None,
        context: str | None = None,
        payload: dict[str, Any] | None = None,
        ordinal: float | None = None,
        keyword_text: str | None = None,
        threshold_rank: float | None = None,
        semantic_text: str | None = None,
        partition: str | None = None,
        threshold_distance: float | None = None,
    ) -> dict[str, Any]:
        """Create a grimoire entry, optionally indexing it in one go.

        Pass `keyword_text` to add an FTS5 row and `semantic_text` to embed a
        vec row. Either, both, or neither — the entry is always created.
        `ordinal` stores a consumer-supplied numeric sort key on the entry
        (timestamps, section numbers, measurements).
        """
        if keyword_text is None and threshold_rank is not None:
            raise ValueError("`threshold_rank` requires `keyword_text`.")
        if semantic_text is None and (
            partition is not None or threshold_distance is not None
        ):
            raise ValueError(
                "`partition` and `threshold_distance` require `semantic_text`."
            )

        db_path = _resolve_db(mnt, db)
        with Grimoire.open(db_path, embedder=embed.build_embedder(mnt.models_dir)) as g:
            [created] = g.add(
                [
                    Entry(
                        id=None,
                        group_key=group_key,
                        group_ref=group_ref,
                        payload=payload,
                        context=context,
                        ordinal=ordinal,
                    )
                ]
            )
            if keyword_text is not None:
                [created] = g.keyword(
                    [(created.id, keyword_text)], threshold_rank=threshold_rank
                )
            if semantic_text is not None:
                [created] = g.embed(
                    [(created.id, semantic_text)],
                    partition=partition,
                    threshold_distance=threshold_distance,
                )
        return asdict(created)

    @mcp.tool
    def entry_update(
        entry_id: str,
        db: str | None = None,
        group_key: str | None = None,
        group_ref: str | None = None,
        payload: dict[str, Any] | None = None,
        context: str | None = None,
        ordinal: float | None = None,
        keyword_text: str | None = None,
        threshold_rank: float | None = None,
        semantic_text: str | None = None,
        partition: str | None = None,
        threshold_distance: float | None = None,
        put: bool = False,
    ) -> dict[str, Any]:
        """Update an entry; optionally (re-)index its keyword or semantic text.

        Default mode is partial-update: unspecified entry fields are preserved.
        Pass `put=True` to switch to replace mode for the entry fields
        (group_key, group_ref, payload, context, ordinal).

        Indexing is decoupled from `put`: passing `keyword_text` always
        replaces the FTS5 row, and `semantic_text` always replaces the vec
        row. Leaving them off preserves the existing index rows as-is.
        """
        if keyword_text is None and threshold_rank is not None:
            raise ValueError("`threshold_rank` requires `keyword_text`.")
        if semantic_text is None and (
            partition is not None or threshold_distance is not None
        ):
            raise ValueError(
                "`partition` and `threshold_distance` require `semantic_text`."
            )

        db_path = _resolve_db(mnt, db)
        with Grimoire.open(db_path, embedder=embed.build_embedder(mnt.models_dir)) as g:
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
                    ordinal=ordinal,
                )
            else:
                merged = replace(
                    current,
                    group_key=current.group_key if group_key is None else group_key,
                    group_ref=current.group_ref if group_ref is None else group_ref,
                    payload=current.payload if payload is None else payload,
                    context=current.context if context is None else context,
                    ordinal=current.ordinal if ordinal is None else ordinal,
                )

            [returned] = g.update([merged])
            if keyword_text is not None:
                g.keyword([(returned.id, keyword_text)], threshold_rank=threshold_rank)
            if semantic_text is not None:
                g.embed(
                    [(returned.id, semantic_text)],
                    partition=partition,
                    threshold_distance=threshold_distance,
                )

            [returned] = g.fetch(Filters(id=[returned.id]), limit=1)
        return asdict(returned)

    @mcp.tool
    def entry_delete(entry_id: str, db: str | None = None) -> dict[str, Any]:
        """Delete a grimoire entry by id.

        Idempotent — a missing id returns `deleted=false`.
        """
        db_path = _resolve_db(mnt, db)
        with Grimoire.open(db_path, embedder=embed.build_embedder(mnt.models_dir)) as g:
            removed = g.remove([entry_id])
        return {"id": entry_id, "deleted": bool(removed)}

    @mcp.tool
    def search_keyword(
        query: str,
        db: str | None = None,
        group_keys: list[str] | None = None,
        group_refs: list[str] | None = None,
        ids: list[str] | None = None,
        ordinal_gte: float | None = None,
        ordinal_lte: float | None = None,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        """Keyword search via FTS5 BM25.

        Supports filtering by group_key, group_ref, id, and ordinal range.
        `rank` is the BM25 score (higher = better, non-negative).
        """
        db_path = _resolve_db(mnt, db)
        filters = Filters(
            id=ids or None,
            group_key=group_keys or None,
            group_ref=group_refs or None,
            ordinal_gte=ordinal_gte,
            ordinal_lte=ordinal_lte,
        )
        # Quote-wrap each word token so apostrophes, punctuation, and bareword
        # FTS5 operators (AND/OR/NOT/NEAR/*) can't reach the parser. Join with
        # OR so casual prose matches any-of, not all-of; BM25 still ranks by
        # aggregate match strength.
        fts_query = " OR ".join(f'"{t}"' for t in re.findall(r"\w+", query))
        if not fts_query:
            return []

        with Grimoire.open(db_path, embedder=embed.build_embedder(mnt.models_dir)) as g:
            hits = g.keyword_search(fts_query, filters=filters, limit=limit)
        return [{"entry": asdict(h.entry), "rank": h.score} for h in hits]

    @mcp.tool
    def search_semantic(
        query: str,
        db: str | None = None,
        partition: str | None = None,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        """Semantic search via vec0 KNN, narrowable by partition.

        `distance` is the raw vector distance (lower = better, non-negative).
        """
        db_path = _resolve_db(mnt, db)
        with Grimoire.open(db_path, embedder=embed.build_embedder(mnt.models_dir)) as g:
            hits = g.semantic_search(query, partition=partition, limit=limit)
        return [{"entry": asdict(h.entry), "distance": h.distance} for h in hits]

    return mcp

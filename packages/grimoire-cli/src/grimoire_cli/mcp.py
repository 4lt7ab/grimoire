"""MCP server surface for a grimoire mount.

Mirrors the library's read+write methods as FastMCP tools, scoped to a single
mount picked at server boot. Mount administration (create/destroy/add/remove)
stays CLI-only; the MCP surface operates on existing databases.
"""

import re
from dataclasses import asdict
from pathlib import Path
from typing import Any

from fastmcp import FastMCP
from grimoire.data.entry import Entry, EntryIndex, Filters
from grimoire.grimoire import Grimoire

from grimoire_cli import embed, mount, telemetry


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


def _tokenize_fts(query: str) -> str:
    return " OR ".join(f'"{t}"' for t in re.findall(r"\w+", query))


def _build_filters(
    equals: dict[str, list[Any]] | None,
    gte: dict[str, Any] | None,
    lte: dict[str, Any] | None,
) -> Filters | None:
    if not (equals or gte or lte):
        return None
    return Filters(equals=equals, gte=gte, lte=lte)


def _pair_index(
    entries: list[Entry], indexes: list[EntryIndex]
) -> list[dict[str, Any]]:
    return [
        {"entry": asdict(e), "index": asdict(i)}
        for e, i in zip(entries, indexes, strict=True)
    ]


def _pair_hits(entries: list[Entry], hits: list, key: str) -> list[dict[str, Any]]:
    return [
        {"entry": asdict(e), key: getattr(h, key)}
        for e, h in zip(entries, hits, strict=True)
    ]


def _coerce_ord(
    ord_: list[Any] | None,
) -> tuple[Any, Any, Any, Any, Any] | None:
    if ord_ is None:
        return None
    if len(ord_) != 5:
        raise ValueError("`ord` must be a 5-element list")
    return (ord_[0], ord_[1], ord_[2], ord_[3], ord_[4])


def build_server(mnt: mount.Mount) -> FastMCP:
    """Construct a FastMCP server exposing the grimoire mount's read+write surface."""
    mcp = FastMCP("grimoire")
    tel = telemetry.build_telemetry()

    def _open(db: str | None) -> Grimoire:
        return Grimoire.open(
            _resolve_db(mnt, db),
            embedder=embed.build_embedder(mnt.models_dir),
            telemetry=tel,
        )

    # ------------------------------------------------------------------
    # Inspection
    # ------------------------------------------------------------------

    @mcp.tool
    def info(db: str | None = None) -> dict[str, Any]:
        """Show metadata for a grimoire database.

        Returns the embedder lock, schema version, per-sidecar row counts,
        file path, and file size. `db` selects a named DB in the mount;
        omit for the default DB.
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

    # ------------------------------------------------------------------
    # Entry CRUD
    # ------------------------------------------------------------------

    @mcp.tool
    def add(
        data: Any = None,
        ref: str | None = None,
        ord: list[Any] | None = None,
        match: str | None = None,
        search: str | None = None,
        db: str | None = None,
    ) -> dict[str, Any]:
        """Create a grimoire entry and optionally PUT-index its sidecars.

        `data` is a JSON-serializable value stored in entry.data.

        The remaining kwargs are forwarded to `index()`. Supplying either of
        `ref` or `ord` PUT-replaces the entry_idx row (omitted columns
        become NULL). `ord` is a 5-element list
        `[ordinal_1, ordinal_2, ordinal_3, ordinal_4, ordinal_5]`; in-list
        nulls write NULL to that column. The columns are BLOB-affinity, so
        any JSON-serializable scalar is stored verbatim. `match` replaces
        the entry_fts row; `search` embeds the text and replaces the
        entry_vec row.
        """
        with _open(db) as g:
            [created] = g.add([Entry(uniq_id=None, data=data)])
            if any(v is not None for v in (ref, ord, match, search)):
                g.index(
                    created.uniq_id,
                    ref=ref,
                    ord=_coerce_ord(ord),
                    match=match,
                    search=search,
                )
        return asdict(created)

    @mcp.tool
    def update(
        uniq_id: str,
        data: Any = None,
        ref: str | None = None,
        ord: list[Any] | None = None,
        match: str | None = None,
        search: str | None = None,
        db: str | None = None,
    ) -> dict[str, Any]:
        """Update an entry's data and/or PUT-index its sidecars.

        - `data`: replaces the entry's `data` column. **Pass `null` (or
          omit) to leave it alone** — MCP/JSON can't distinguish those two
          cases, so to explicitly null `data`, drop to the CLI or library.
        - `ref`, `ord`, `match`, `search`: same PUT semantics as `add`.
          Supplying either of `ref` or `ord` wholesale-replaces the
          `entry_idx` row; omitted columns become NULL.

        The entry must already exist; otherwise raises.
        """
        with _open(db) as g:
            if data is not None:
                updated = g.update([Entry(uniq_id=uniq_id, data=data)])
                if not updated:
                    raise ValueError(f"No entry with uniq_id {uniq_id!r}")
                current = updated[0]
            else:
                existing = g.get([uniq_id])
                if not existing:
                    raise ValueError(f"No entry with uniq_id {uniq_id!r}")
                current = existing[0]

            if any(v is not None for v in (ref, ord, match, search)):
                g.index(
                    uniq_id,
                    ref=ref,
                    ord=_coerce_ord(ord),
                    match=match,
                    search=search,
                )
        return asdict(current)

    @mcp.tool
    def get(uniq_ids: list[str], db: str | None = None) -> list[dict[str, Any]]:
        """Fetch entries by uniq_id. Returns only the ones that exist."""
        with _open(db) as g:
            return [asdict(e) for e in g.get(uniq_ids)]

    @mcp.tool
    def remove(uniq_ids: list[str], db: str | None = None) -> list[str]:
        """Delete entries. Sidecar rows are cascade-cleaned by DB trigger.

        Returns the ids that were actually removed.
        """
        with _open(db) as g:
            return g.remove(uniq_ids)

    # ------------------------------------------------------------------
    # Reads / search
    # ------------------------------------------------------------------

    @mcp.tool
    def query(
        equals: dict[str, list[Any]] | None = None,
        gte: dict[str, Any] | None = None,
        lte: dict[str, Any] | None = None,
        cursor: str | None = None,
        limit: int = 100,
        db: str | None = None,
    ) -> list[dict[str, Any]]:
        """Browse entry_idx rows with optional dict-driven filters.

        Returns `[{entry, index}, ...]` ordered by `uniq_id` ASC.

        - `equals` keys may name any entry_idx column; the entry must match
          one of the listed values for that column.
        - `gte` / `lte` keys must name one of `ordinal_1`..`ordinal_5` and
          apply `>= value` / `<= value`. Values may be any type SQLite can
          compare; comparison follows class precedence (NULL < INT/REAL <
          TEXT < BLOB).
        - `cursor` pages by id (`uniq_id > cursor`); pair with the default
          ordering for forward paging.
        """
        filters = _build_filters(equals, gte, lte)
        with _open(db) as g:
            entries, indexes = g.query(filters, limit=limit, cursor=cursor)
        return _pair_index(entries, indexes)

    @mcp.tool
    def fetch(uniq_refs: list[str], db: str | None = None) -> list[dict[str, Any]]:
        """Fetch entries whose entry_idx row has uniq_ref in the given list.

        Returns `[{entry, index}, ...]`. `uniq_ref` is sparse-unique (UNIQUE
        partial index over the non-NULL rows), so each ref maps to at most
        one entry; entries without an entry_idx row are excluded.
        """
        with _open(db) as g:
            entries, indexes = g.fetch(uniq_refs)
        return _pair_index(entries, indexes)

    @mcp.tool
    def match(
        query: str,
        equals: dict[str, list[Any]] | None = None,
        gte: dict[str, Any] | None = None,
        lte: dict[str, Any] | None = None,
        limit: int = 10,
        db: str | None = None,
    ) -> list[dict[str, Any]]:
        """FTS5 BM25 keyword search.

        Returns `[{entry, score}, ...]` in rank order. The query is
        tokenized so apostrophes, punctuation, and bareword FTS5 operators
        (AND/OR/NOT/NEAR/*) can't reach the parser. Filters apply via
        JOIN to entry_idx (see `query` for filter semantics).
        """
        fts_query = _tokenize_fts(query)
        if not fts_query:
            return []
        filters = _build_filters(equals, gte, lte)
        with _open(db) as g:
            entries, hits = g.match(fts_query, filters=filters, limit=limit)
        return _pair_hits(entries, hits, "score")

    @mcp.tool
    def search(
        query: str,
        limit: int = 10,
        db: str | None = None,
    ) -> list[dict[str, Any]]:
        """vec0 KNN semantic search.

        Returns `[{entry, distance}, ...]` nearest-first. `distance` is
        the raw vector distance (lower = better, non-negative).
        """
        with _open(db) as g:
            entries, hits = g.search(query, limit=limit)
        return _pair_hits(entries, hits, "distance")

    return mcp

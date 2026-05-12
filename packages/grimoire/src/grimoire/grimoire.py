import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path

import sqlite_vec

from grimoire.data import entry, meta, schema
from grimoire.data.entry import (
    Entry,
    Filters,
    KeywordHit,
    SemanticHit,
)
from grimoire.embed import Embedder
from grimoire.errors import GrimoireMismatch, GrimoireNotFound


@dataclass(frozen=True, slots=True)
class Peek:
    model: str
    dimension: int
    schema_version: int
    entry_count: int
    group_counts: dict[str | None, int]
    partition_counts: dict[str | None, int]


class Grimoire:
    def __init__(self, conn: sqlite3.Connection, embedder: Embedder) -> None:
        self._conn = conn
        self.embedder = embedder

    def __enter__(self) -> "Grimoire":
        return self

    def __exit__(self, exc_type, *_) -> None:
        if exc_type is None:
            self._conn.commit()
        else:
            self._conn.rollback()

    def add(self, entries: list[Entry]) -> list[Entry]:
        return entry.add(self._conn, entries)

    def update(self, entries: list[Entry]) -> list[Entry]:
        return entry.update(self._conn, entries)

    def remove(self, ids: list[str]) -> list[str]:
        return entry.remove(self._conn, ids)

    def fetch(
        self,
        filters: Filters | None = None,
        limit: int = 100,
    ) -> list[Entry]:
        return entry.fetch(self._conn, filters, limit)

    def keyword_remove(self, ids: list[str]) -> list[str]:
        """Delete entry_fts rows for the given ids. Returns the ids that had rows.

        Entries themselves are not affected. Empty `ids` is a no-op.
        """
        return entry.keyword_remove(self._conn, ids)

    def embed_remove(self, ids: list[str]) -> list[str]:
        """Delete entry_vec rows for the given ids. Returns the ids that had rows.

        Entries themselves are not affected. Empty `ids` is a no-op.
        """
        return entry.embed_remove(self._conn, ids)

    def keyword(
        self,
        items: list[tuple[str, str]] | None = None,
        *,
        threshold_rank: float | None = None,
    ) -> list[Entry]:
        """Index (or re-index) entries' keyword text from (id, keyword_text) pairs.

        Empty or None `items` is a no-op. Each id must refer to an existing
        entry. Existing fts rows for these ids are replaced. `threshold_rank`
        is stored on every row written in this call.
        """
        if not items:
            return []

        ids = [i for i, _ in items]
        existing = self._conn.execute(
            "SELECT id FROM entry WHERE id IN (SELECT value FROM json_each(?))",
            (json.dumps(ids),),
        ).fetchall()
        known = {r["id"] for r in existing}
        for entry_id in ids:
            if entry_id not in known:
                raise ValueError(f"No entry with id {entry_id!r}")

        return entry.keyword(self._conn, items, threshold_rank=threshold_rank)

    def embed(
        self,
        items: list[tuple[str, str]] | None = None,
        *,
        partition: str | None = None,
        threshold_distance: float | None = None,
    ) -> list[Entry]:
        """Embed (or re-embed) entries from (id, semantic_text) pairs into the given partition.

        Empty or None `items` is a no-op. Each id must refer to an existing
        entry; the given text is embedded and stored on the vec row. Existing
        vec rows for these ids are replaced. `threshold_distance` is stored on
        every row written in this call.
        """
        if not items:
            return []

        ids = [i for i, _ in items]
        texts = [t for _, t in items]

        existing = self._conn.execute(
            "SELECT id FROM entry WHERE id IN (SELECT value FROM json_each(?))",
            (json.dumps(ids),),
        ).fetchall()
        known = {r["id"] for r in existing}
        for entry_id in ids:
            if entry_id not in known:
                raise ValueError(f"No entry with id {entry_id!r}")

        embeddings = self.embedder.embed_many(texts)
        return entry.embed(
            self._conn,
            [(i, t, v) for (i, t), v in zip(items, embeddings, strict=True)],
            partition=partition,
            threshold_distance=threshold_distance,
        )

    def keyword_search(
        self,
        query: str,
        filters: Filters | None = None,
        limit: int | None = None,
    ) -> list[KeywordHit]:
        return entry.keyword_search(self._conn, query, filters, limit)

    def semantic_search(
        self,
        query: str,
        partition: str | None = None,
        limit: int = 10,
    ) -> list[SemanticHit]:
        return entry.semantic_search(
            self._conn,
            self.embedder.embed(query),
            partition,
            limit,
        )


def peek(path: str | Path) -> Peek:
    """Inspect a grimoire file without loading an embedder.

    Returns model, dimension, schema version, entry count, per-group counts
    (from `entry`), and per-partition counts (from `entry_vec`). Raises
    `GrimoireNotFound` if the file does not exist or has not been
    initialized.
    """
    p = Path(path)
    if not p.exists():
        raise GrimoireNotFound(f"No grimoire at {p}")

    conn = sqlite3.connect(p)
    conn.row_factory = sqlite3.Row
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    conn.enable_load_extension(False)

    try:
        if schema.read_version(conn) == 0:
            raise GrimoireNotFound(f"{p} is not an initialized grimoire")

        schema.validate(conn)
        model = meta.fetch(conn, "model")
        dimension_str = meta.fetch(conn, "dimension")

        if model is None or dimension_str is None:
            raise GrimoireNotFound(f"{p} is missing its embedder lock")

        entry_count = conn.execute("SELECT COUNT(*) FROM entry").fetchone()[0]
        group_rows = conn.execute(
            "SELECT group_key, COUNT(*) AS n FROM entry GROUP BY group_key "
            "ORDER BY group_key IS NULL, group_key"
        ).fetchall()
        partition_rows = conn.execute(
            "SELECT partition, COUNT(*) AS n FROM entry_vec GROUP BY partition "
            "ORDER BY partition IS NULL, partition"
        ).fetchall()

        return Peek(
            model=model,
            dimension=int(dimension_str),
            schema_version=schema.read_version(conn),
            entry_count=entry_count,
            group_counts={r["group_key"]: r["n"] for r in group_rows},
            partition_counts={r["partition"]: r["n"] for r in partition_rows},
        )
    finally:
        conn.close()


def open(path: str | Path, *, embedder: Embedder) -> Grimoire:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row

    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    conn.enable_load_extension(False)

    if schema.read_version(conn) == 0:
        schema.create(conn, model=embedder.model, dimension=embedder.dimension)
    else:
        schema.validate(conn)
        stored_model = meta.fetch(conn, "model")
        stored_dimension = int(meta.fetch(conn, "dimension"))
        if stored_model != embedder.model or stored_dimension != embedder.dimension:
            raise GrimoireMismatch(
                f"Embedder reports model={embedder.model!r} dimension={embedder.dimension}, "
                f"file locked to model={stored_model!r} dimension={stored_dimension}."
            )

    return Grimoire(conn, embedder=embedder)
